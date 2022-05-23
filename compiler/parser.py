import os
from sys import stderr
import sys
from typing import Callable, NoReturn, TypeVar

from .primitives import nodes, Node, TT, Token, Config, Type, types, JARARACA_PATH, BUILTIN_WORDS
from .utils import extract_module_from_file_name

class Parser:
	__slots__ = ('words', 'config', 'idx', 'parsed_tops', 'module_path')
	def __init__(self, words:list[Token], config:Config, module_path:str) -> None:
		self.words      :list[Token] = words
		self.config     :Config      = config
		self.idx        :int         = 0
		self.parsed_tops:list[Node]  = []
		self.module_path:str         = module_path
	def adv(self) -> Token:
		"""advance current word, and return what was current"""
		ret = self.current
		self.idx+=1
		return ret
	@property
	def current(self) -> Token:
		return self.words[self.idx]
	def parse(self) -> nodes.Module:
		#first, include std.builtin's if I am not std.builtin
		if self.module_path != 'std.builtin':
			builtins = extract_module_from_file_name(os.path.join(JARARACA_PATH,'std','builtin.ja'),self.config,'std.builtin')
			self.parsed_tops.append(nodes.FromImport('std.builtin', '<built-in>', builtins, BUILTIN_WORDS, self.current.loc))

		while self.current == TT.NEWLINE:
			self.adv() # skip newlines
		while self.current.typ != TT.EOF:
			top = self.parse_top()
			if top is not None:
				self.parsed_tops.append(top)
			while self.current == TT.NEWLINE:
				self.adv() # skip newlines
		return nodes.Module(tuple(self.parsed_tops),self.module_path)
	def parse_top(self) -> 'Node|None':
		if self.current.equals(TT.KEYWORD, 'fun'):
			return self.parse_fun()
		elif self.current.equals(TT.KEYWORD, 'use'):
			self.adv()
			if self.current.typ != TT.WORD:
				print(f"ERROR: {self.current.loc} expected a name of function to use", file=stderr)
				sys.exit(5)
			name = self.adv()
			#name(type, type) -> type
			if self.current.typ != TT.LEFT_PARENTHESIS:
				print(f"ERROR: {self.current.loc} expected '(' after 'use' keyword and a function name", file=stderr)
				sys.exit(6)
			self.adv()
			input_types:list[Type] = []
			while self.current != TT.RIGHT_PARENTHESIS:
				input_types.append(self.parse_type())
				if self.current == TT.RIGHT_PARENTHESIS:
					break
				if self.current != TT.COMMA:
					print(f"ERROR: {self.current.loc} expected ',' or ')'", file=stderr)
					sys.exit(7)
				self.adv()
			self.adv()
			output_type:Type = types.VOID
			if self.current.typ == TT.RIGHT_ARROW: # provided any output types
				self.adv()
				output_type = self.parse_type()
			return nodes.Use(name, tuple(input_types), output_type)
		elif self.current.equals(TT.KEYWORD, 'const'):
			self.adv()
			if self.current.typ != TT.WORD:
				print(f"ERROR: {self.current.loc} expected name of constant after keyword 'const'", file=stderr)
				sys.exit(8)
			name = self.adv()
			value = self.parse_CTE()
			return nodes.Const(name, value)
		elif self.current.equals(TT.KEYWORD, 'import'):
			self.adv()
			path,nam,module = self.parse_module_path()
			return nodes.Import(path,nam,module)
		elif self.current.equals(TT.KEYWORD, 'from'):
			loc = self.adv().loc
			path,nam,module = self.parse_module_path()
			if not self.current.equals(TT.KEYWORD, 'import'):
				print(f"ERROR: {self.current.loc} expected keyword 'import' after path in 'from ... import ...' top", file=stderr)
				sys.exit(9)
			self.adv()
			if self.current != TT.WORD:
				print(f"ERROR: {self.current.loc} expected word, to import after keyword 'import' in 'from ... import ...' top", file=stderr)
				sys.exit(10)
			names = [self.adv().operand]
			while self.current == TT.COMMA:
				self.adv()
				if self.current != TT.WORD:
					print(f"ERROR: {self.current.loc} expected word, to import after comma in 'from ... import ...' top", file=stderr)
					sys.exit(11)
				names.append(self.adv().operand)
			return nodes.FromImport(path,nam,module,tuple(names),loc)

		elif self.current.equals(TT.KEYWORD, 'struct'):
			loc = self.adv().loc
			if self.current.typ != TT.WORD:
				print(f"ERROR: {self.current.loc} expected name of a structure after keyword 'struct'", file=stderr)
				sys.exit(12)
			name = self.adv()
			generics = []
			if self.current == TT.TILDE:
				self.adv()
				while self.current != TT.TILDE:
					if self.current != TT.PERCENT:
						print(f"ERROR: {self.current.loc} expected '%' as prefix before generic name", file=stderr)
						sys.exit(13)
					self.adv()
					if self.current != TT.WORD:
						print(f"ERROR: {self.current.loc} expected name of generic type in 'struct ...~...~'", file=stderr)
						sys.exit(14)
					generics.append(types.Generic(self.adv().operand))
					if self.current != TT.COMMA:
						break
					self.adv()
				self.adv()
			static:list[nodes.Assignment] = []
			vars:list[nodes.TypedVariable] = []
			functions:list[nodes.Fun] = []
			for var in self.block_parse_helper(self.parse_struct_statement):
				if isinstance(var,nodes.Assignment):
					static.append(var)
				elif isinstance(var,nodes.TypedVariable):
					vars.append(var)
				elif isinstance(var,nodes.Fun):
					functions.append(var)
				else:
					assert False, "unreachable"
			return nodes.Struct(loc, name, tuple(vars), tuple(static), tuple(functions), tuple(generics))
		elif self.current.equals(TT.KEYWORD, 'mix'):
			loc = self.adv().loc
			if self.current.typ != TT.WORD:
				print(f"ERROR: {self.current.loc} expected name of mix after keyword 'mix'", file=stderr)
				sys.exit(15)
			name = self.adv()
			funs = self.block_parse_helper(self.parse_mix_statement)
			return nodes.Mix(loc,name,tuple(funs))

		else:
			print(f"ERROR: {self.current.loc} unrecognized top-level structure while parsing", file=stderr)
			sys.exit(16)
	def parse_mix_statement(self) -> 'nodes.ReferTo':
		if self.current != TT.WORD:
			print(f"ERROR: {self.current.loc} expected name of a function while parsing mix", file=stderr)
			sys.exit(17)
		return self.parse_reference()
	def parse_module_path(self) -> 'tuple[str,str,nodes.Module]':
		if self.current.typ != TT.WORD:
			print(f"ERROR: {self.current.loc} expected name of a packet at the start of module path", file=stderr)
			sys.exit(18)
		next_level = self.adv().operand
		path:str = next_level
		link_path = os.path.join(JARARACA_PATH,'packets',next_level+'.link')
		if not os.path.exists(link_path):
			print(f"ERROR: {self.current.loc} module '{path}' was not found in at '{link_path}'", file=stderr)
			sys.exit(19)
		with open(link_path,'r') as f:
			file_path = f.read()

		while self.current == TT.DOT:
			self.adv()
			if self.current.typ != TT.WORD:
				print(f"ERROR: {self.current.loc} expected name of the next module in the hierarchy after dot", file=stderr)
				sys.exit(20)
			if not os.path.isdir(file_path):
				print(f"ERROR: {self.current.loc} module '{path}' was not found in at '{file_path}'", file=stderr)
				sys.exit(21)
			next_level = self.adv().operand
			path += '.' + next_level
			file_path = os.path.join(file_path,next_level)
		if not os.path.isdir(file_path):
			file_path += '.ja'
		else:
			file_path = os.path.join(file_path,'__init__.ja')
		if not os.path.exists(file_path):
			print(f"ERROR: {self.current.loc} module '{path}' was not found in at '{file_path}'", file=stderr)
			sys.exit(22)
		try:
			module = extract_module_from_file_name(file_path,self.config,path)
		except RecursionError:
			print(f"ERROR: {self.current.loc} recursion depth exceeded (circular import)", file=stderr)
			sys.exit(23)
		return path,next_level,module
	def parse_fun(self) -> nodes.Fun:
		self.adv()
		if self.current.typ != TT.WORD:
			print(f"ERROR: {self.current.loc} expected name of a function after keyword 'fun'", file=stderr)
			sys.exit(24)
		name = self.adv()
		#name(tv, tv) -> type
		if self.current.typ != TT.LEFT_PARENTHESIS:
			print(f"ERROR: {self.current.loc} expected '(' after 'fun' and function name", file=stderr)
			sys.exit(25)
		self.adv()
		input_types:list[nodes.TypedVariable] = []
		while self.current != TT.RIGHT_PARENTHESIS:
			input_types.append(self.parse_typed_variable())
			if self.current == TT.RIGHT_PARENTHESIS:
				break
			if self.current != TT.COMMA:
				print(f"ERROR: {self.current.loc} expected ',' or ')'", file=stderr)
				sys.exit(26)
			self.adv()
		self.adv()
		output_type:Type = types.VOID
		if self.current.typ == TT.RIGHT_ARROW: # provided any output types
			self.adv()
			output_type = self.parse_type()
		code = self.parse_code_block()
		return nodes.Fun(name, tuple(input_types), output_type, code)

	def parse_struct_statement(self) -> 'nodes.TypedVariable|nodes.Assignment|nodes.Fun':
		if self.next is not None:
			if self.next == TT.COLON:
				var = self.parse_typed_variable()
				if self.current == TT.EQUALS:
					self.adv()
					expr = self.parse_expression()
					return nodes.Assignment(var,expr)
				return var
		if self.current.equals(TT.KEYWORD, 'fun'):
			return self.parse_fun()
		print(f"ERROR: {self.current.loc} unrecognized struct statement", file=stderr)
		sys.exit(27)
	def parse_CTE(self) -> int:
		def parse_term_int_CTE() -> int:
			if self.current == TT.INTEGER:
				return int(self.adv().operand)
			if self.current == TT.WORD:
				def find_a_const(tops:list[Node]) -> int|None:
					for top in tops:
						if isinstance(top, nodes.Const):
							if top.name == self.current:
								self.adv()
								return top.value
						if isinstance(top, nodes.FromImport):
							for name in top.imported_names:
								if name == self.current:
									return find_a_const(list(top.module.tops))
					return None
				i = find_a_const(self.parsed_tops)
				if i is not None: return i
			print(f"ERROR: {self.current.loc} term '{self.current}' is not supported in compile-time-evaluation", file=stderr)
			sys.exit(28)
		operations = (
			TT.PLUS,
			TT.MINUS,

			TT.ASTERISK,

			TT.DOUBLE_SLASH,
			TT.PERCENT,
		)
		left:int = parse_term_int_CTE()
		while self.current.typ in operations:
			op_token = self.adv()
			right = parse_term_int_CTE()
			if   op_token == TT.PLUS        : left = left +  right
			elif op_token == TT.MINUS       : left = left -  right
			elif op_token == TT.ASTERISK    : left = left *  right
			elif op_token == TT.DOUBLE_SLASH: left = left // right
			elif op_token == TT.PERCENT: left = left %  right
			else:
				print(f"ERROR: {self.current.loc} unknown operation '{op_token}' in compile time evaluation", file=stderr)
		return left
	def parse_code_block(self) -> nodes.Code:
		return nodes.Code(self.block_parse_helper(self.parse_statement))
	@property
	def next(self) -> 'Token | None':
		if len(self.words)>self.idx+1:
			return self.words[self.idx+1]
		return None
	def parse_statement(self) -> 'Node|Token':
		if self.next is not None:#variables
			if self.next == TT.COLON:
				var = self.parse_typed_variable()
				if self.current.typ != TT.EQUALS:#var:type
					return nodes.Declaration(var)
				#var:type = value
				self.adv()
				value = self.parse_expression()
				return nodes.Assignment(var, value)
			if self.next == TT.EQUALS:
				if self.current == TT.WORD:
					variable = self.adv()
					loc = self.adv().loc# name = expression
					value = self.parse_expression()
					return nodes.VariableSave(variable,value,loc)
		if self.current == TT.LEFT_SQUARE_BRACKET:
			self.adv()
			times = self.parse_expression()
			if self.current != TT.RIGHT_SQUARE_BRACKET:
				print(f"ERROR: {self.current.loc} expected ']'",file=stderr)
				sys.exit(29)
			self.adv()
			var = self.parse_typed_variable()
			return nodes.Declaration(var,times)
		if self.current.equals(TT.KEYWORD, 'if'):
			return self.parse_if()
		if self.current.equals(TT.KEYWORD, 'alias'):
			self.adv()
			if self.current != TT.WORD:
				print(f"ERROR: {self.current.loc} expected name after keyword 'alias'",file=stderr)
				sys.exit(30)
			name = self.adv()
			if self.current != TT.EQUALS:
				print(f"ERROR: {self.current.loc} expected '=' after name and keyword 'alias'",file=stderr)
				sys.exit(31)
			self.adv()
			expr = self.parse_expression()
			return nodes.Alias(name,expr)
		if self.current.equals(TT.KEYWORD, 'while'):
			return self.parse_while()
		elif self.current.equals(TT.KEYWORD, 'return'):
			loc = self.adv().loc
			return nodes.Return(loc,self.parse_expression())
		expr = self.parse_expression()
		if self.current == TT.EQUALS:
			loc = self.adv().loc
			return nodes.Save(expr, self.parse_expression(),loc)
		return nodes.ExprStatement(expr)
	def parse_if(self) -> Node:
		loc = self.adv().loc
		condition = self.parse_expression()
		if_code = self.parse_code_block()
		if self.current.equals(TT.KEYWORD, 'elif'):
			else_block = self.parse_if()
			return nodes.If(loc, condition, if_code, else_block)
		if self.current.equals(TT.KEYWORD, 'else'):
			self.adv()
			else_code = self.parse_code_block()
			return nodes.If(loc, condition, if_code, else_code)
		return nodes.If(loc, condition, if_code)
	def parse_while(self) -> Node:
		loc = self.adv().loc
		condition = self.parse_expression()
		code = self.parse_code_block()
		return nodes.While(loc, condition, code)
	def parse_typed_variable(self) -> nodes.TypedVariable:
		if self.current != TT.WORD:
			print(f"ERROR: {self.current.loc} expected variable name in typed variable", file=stderr)
			sys.exit(32)
		name = self.adv()
		if self.current.typ != TT.COLON:
			print(f"ERROR: {self.current.loc} expected colon ':'",file=stderr)
			sys.exit(33)
		self.adv()#type
		typ = self.parse_type()

		return nodes.TypedVariable(name, typ)
	def parse_type(self) -> Type:
		if self.current == TT.WORD:
			const = {
				'void' : types.VOID,
				'bool' : types.BOOL,
				'char' : types.CHAR,
				'short': types.SHORT,
				'str'  : types.STR,
				'int'  : types.INT,
			}
			out:Type|None = const.get(self.current.operand) # for now that is enough

			if out is None:
				name = self.adv().operand
				#parse ~type,type,...~
				if self.current != TT.TILDE:
					return types.Struct(name,())
				self.adv()
				generics = []
				while self.current != TT.TILDE:
					generics.append(self.parse_type())
					if self.current != TT.COMMA:
						break
					self.adv()
				self.adv()
				return types.Struct(name,tuple(generics))

			self.adv()
			return out
		elif self.current == TT.LEFT_SQUARE_BRACKET:#array
			self.adv()
			if self.current == TT.RIGHT_SQUARE_BRACKET:
				size = 0
			else:
				size = self.parse_CTE()
			if self.current != TT.RIGHT_SQUARE_BRACKET:
				print(f"ERROR: {self.current.loc} expected ']', '[' was opened and never closed", file=stderr)
				sys.exit(34)
			self.adv()
			typ = self.parse_type()
			return types.Array(size,typ)
		elif self.current.typ == TT.LEFT_PARENTHESIS:
			self.adv()
			input_types:list[Type] = []
			while self.current != TT.RIGHT_PARENTHESIS:
				input_types.append(self.parse_type())
				if self.current == TT.RIGHT_PARENTHESIS:
					break
				if self.current != TT.COMMA:
					print(f"ERROR: {self.current.loc} expected ',' or ')'", file=stderr)
					sys.exit(35)
				self.adv()
			self.adv()
			return_type:Type = types.VOID
			if self.current.typ == TT.RIGHT_ARROW: # provided any output types
				self.adv()
				return_type = self.parse_type()
			return types.Fun(tuple(input_types),return_type)
		elif self.current == TT.ASTERISK:
			self.adv()
			out = self.parse_type()
			return types.Ptr(out)
		#%T
		elif self.current == TT.PERCENT:
			self.adv()
			if self.current != TT.WORD:
				print(f"ERROR: {self.current.loc} expected generic type name", file=stderr)
				sys.exit(36)
			name = self.adv().operand
			return types.Generic(name)
		else:
			print(f"ERROR: {self.current.loc} Unrecognized type", file=stderr)
			sys.exit(37)

	def parse_expression(self) -> 'Node | Token':
		return self.parse_exp0()
	T = TypeVar('T')
	def block_parse_helper(
		self,
		parse_statement:Callable[[], T]
			) -> tuple[T, ...]:
		if self.current.typ != TT.LEFT_CURLY_BRACKET:
			print(f"ERROR: {self.current.loc} expected block starting with '{{'", file=stderr)
			sys.exit(38)
		self.adv()
		statements = []
		while self.current.typ in (TT.SEMICOLON,TT.NEWLINE):
			self.adv()
		while self.current != TT.RIGHT_CURLY_BRACKET:
			statement = parse_statement()
			statements.append(statement)
			if self.current == TT.RIGHT_CURLY_BRACKET:
				break
			if self.current.typ not in (TT.SEMICOLON,TT.NEWLINE):
				print(f"ERROR: {self.current.loc} expected newline, ';' or '}}'", file=stderr)
				sys.exit(39)
			while self.current.typ in (TT.SEMICOLON,TT.NEWLINE):
				self.adv()
		self.adv()
		return tuple(statements)
	def bin_exp_parse_helper(
		self,
		next_exp:Callable[[], Node|Token],
		operations:list[TT]
			) -> Node | Token:
		left = next_exp()
		while self.current.typ in operations:
			op_token = self.adv()
			right = next_exp()
			left = nodes.BinaryExpression(left, op_token, right)
		return left

	def parse_exp0(self) -> 'Node | Token':
		next_exp = self.parse_exp1
		operations = [
			'or',
			'xor',
			'and',
		]
		left = next_exp()
		while self.current == TT.KEYWORD and self.current.operand in operations:
			op_token = self.adv()
			right = next_exp()
			left = nodes.BinaryExpression(left, op_token, right)
		return left

	def parse_exp1(self) -> 'Node | Token':
		next_exp = self.parse_exp2
		return self.bin_exp_parse_helper(next_exp, [
			TT.LESS,
			TT.GREATER,
			TT.DOUBLE_EQUALS,
			TT.NOT_EQUALS,
			TT.LESS_OR_EQUAL,
			TT.GREATER_OR_EQUAL,
		])

	def parse_exp2(self) -> 'Node | Token':
		next_exp = self.parse_exp3
		return self.bin_exp_parse_helper(next_exp, [
			TT.PLUS,
			TT.MINUS,
		])
	def parse_exp3(self) -> 'Node | Token':
		next_exp = self.parse_exp4
		return self.bin_exp_parse_helper(next_exp, [
			TT.ASTERISK,
		])
	def parse_exp4(self) -> 'Node | Token':
		next_exp = self.parse_exp5
		return self.bin_exp_parse_helper(next_exp, [
			TT.DOUBLE_SLASH,
			TT.DOUBLE_GREATER,
			TT.DOUBLE_LESS,
			TT.PERCENT,
		])
	def parse_exp5(self) -> 'Node | Token':
		self_exp = self.parse_exp5
		next_exp = self.parse_exp6
		operations = (
			TT.NOT,
			TT.AT,
		)
		if self.current.typ in operations:
			op_token = self.adv()
			right = self_exp()
			return nodes.UnaryExpression(op_token, right)
		return next_exp()

	def parse_exp6(self) -> 'Node | Token':
		next_exp = self.parse_term
		left = next_exp()
		while self.current.typ in (TT.DOT,TT.LEFT_SQUARE_BRACKET, TT.LEFT_PARENTHESIS):
			if self.current == TT.DOT:
				loc = self.adv().loc
				if self.current != TT.WORD:
					print(f"ERROR: {self.current.loc} expected word after '.'", file=stderr)
					sys.exit(40)
				access = self.adv()
				left = nodes.Dot(left, access,loc)
			elif self.current == TT.LEFT_SQUARE_BRACKET:
				loc = self.adv().loc
				idx = self.parse_expression()
				if self.current != TT.RIGHT_SQUARE_BRACKET:
					print(f"ERROR: {self.current.loc} expected ']', '[' was opened and never closed", file=stderr)
					sys.exit(41)
				self.adv()
				left = nodes.GetItem(left, idx, loc)
			elif self.current == TT.LEFT_PARENTHESIS:
				loc = self.adv().loc
				args = []
				while self.current.typ != TT.RIGHT_PARENTHESIS:
					args.append(self.parse_expression())
					if self.current.typ == TT.RIGHT_PARENTHESIS:
						break
					if self.current.typ != TT.COMMA:
						print(f"ERROR: {self.current.loc} expected ', ' or ')'", file=stderr)
						sys.exit(42)
					self.adv()
				self.adv()
				left = nodes.Call(loc,left, tuple(args))
		return left
	def parse_reference(self) -> nodes.ReferTo:
		if self.current != TT.WORD:
			print(f"ERROR: {self.current.loc} expected word refer to", file=stderr)
			sys.exit(43)
		name = self.adv()
		#parse name<type,type,...>
		generics:list[Type] = []
		if self.current == TT.TILDE:
			self.adv()
			while self.current.typ != TT.TILDE:
				generics.append(self.parse_type())
				if self.current.typ == TT.TILDE:
					break
				if self.current.typ != TT.COMMA:
					print(f"ERROR: {self.current.loc} expected ', ' or '>'", file=stderr)
					sys.exit(44)
				self.adv()
			self.adv()
		return nodes.ReferTo(name, tuple(generics))
	def parse_term(self) -> 'Node | Token':
		if self.current.typ in (TT.INTEGER, TT.STRING, TT.CHARACTER, TT.SHORT):
			token = self.adv()
			return token
		elif self.current.typ == TT.LEFT_PARENTHESIS:
			self.adv()
			expr = self.parse_expression()
			if self.current.typ != TT.RIGHT_PARENTHESIS:
				print(f"ERROR: {self.current.loc} expected ')'", file=stderr)
				sys.exit(45)
			self.adv()
			return expr
		elif self.current == TT.WORD: #name
			return self.parse_reference()
		elif self.current == TT.KEYWORD: # constant singletons like True, False, Null
			name = self.adv()
			return nodes.Constant(name)
		elif self.current == TT.DOLLAR:# cast
			loc = self.adv().loc
			def err() -> NoReturn:
				print(f"ERROR: {self.current.loc} expected ')' after expression in cast", file=stderr)
				sys.exit(46)
			if self.current == TT.LEFT_PARENTHESIS:#the sneaky str conversion
				self.adv()
				length = self.parse_expression()
				if self.current != TT.COMMA:
					print(f"ERROR: {self.current.loc} expected ',' in str conversion", file=stderr)
					sys.exit(47)
				self.adv()
				pointer = self.parse_expression()
				if self.current == TT.COMMA:self.adv()
				if self.current != TT.RIGHT_PARENTHESIS:err()
				self.adv()
				return nodes.StrCast(loc,length,pointer)
			typ = self.parse_type()
			if self.current.typ != TT.LEFT_PARENTHESIS:
				print(f"ERROR: {self.current.loc} expected '(' after type in cast", file=stderr)
				sys.exit(48)
			self.adv()
			expr = self.parse_expression()
			if self.current.typ != TT.RIGHT_PARENTHESIS:err()
			self.adv()
			return nodes.Cast(loc,typ,expr)
		else:
			print(f"ERROR: {self.current.loc} Unexpected token while parsing term", file=stderr)
			sys.exit(49)
