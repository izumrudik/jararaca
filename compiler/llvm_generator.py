from typing import Callable
from .primitives import Node, nodes, TT, Config, Type, types
from dataclasses import dataclass

@dataclass(slots=True, frozen=True)
class TV:#typed value
	ty:Type|None  = None
	val:str = ''
	@property
	def typ(self) -> Type:
		if self.ty is None:
			raise Exception(f"TV has no type: {self}")
		return self.ty
	def __str__(self) -> str:
		if self.ty is None:
			return f"<None TV>"
		if self.ty is types.VOID:
			return f"{self.typ.llvm} 0"
		return f"{self.typ.llvm} {self.val}"
@dataclass(slots=True, frozen=True)
class MixTypeTv(Type):
	funs:list[TV]
	name:str
	def __str__(self) -> str:
		return f"mixTV({self.name})"
	@property
	def llvm(self) -> str:
		raise Exception(f"Mix type does not make sense in llvm, {self}")

imported_modules_paths:'dict[str,GenerateAssembly]' = {}
class GenerateAssembly:
	__slots__ = ('text','module','config', 'funs', 'strings', 'names', 'modules', 'structs')
	def __init__(self, module:nodes.Module, config:Config) -> None:
		self.config   :Config                    = config
		self.module   :nodes.Module              = module
		self.text     :str                       = ''
		self.strings  :list[nodes.Str]           = []
		self.names    :dict[str,TV]              = {}
		self.modules  :dict[int,GenerateAssembly]= {}
		self.structs  :dict[str,nodes.Struct]    = {}
		self.generate_assembly()
	def visit_from_import(self,node:nodes.FromImport) -> TV:
		return TV()
	def visit_import(self, node:nodes.Import) -> TV:
		return TV()
	def visit_fun(self, node:nodes.Fun, name:str|None=None) -> TV:
		if name is None:
			if len(node.generics) == 0:
				return self.visit_fun(node, node.llvmid)
			for generic_fill in node.generic_fills:
				for idx, generic in enumerate(generic_fill):
					types.Generic.fills[node.generics[idx]] = generic
				self.visit_fun(node,types.GenericFun(node).llvmid(generic_fill))
			for generic in node.generics:
				types.Generic.fills.pop(generic, None)# if len(node.generic_fills) = 0, then generic might not be in fills
			return TV()
		old = self.names.copy()
		for arg in node.arg_types:
			self.names[arg.name.operand] = TV(arg.typ,f'%argument{arg.uid}')
		ot = node.return_type
		if node.name.operand == 'main':
			self.text += f"""
define i64 @main(i32 %0, i8** %1){{;entry point
	call void @GC_init()
	call void {self.module.llvmid}()
	%3 = zext i32 %0 to {types.INT.llvm}
	%4 = bitcast i8** %1 to {types.Ptr(types.Array(types.Ptr(types.Array(types.CHAR)))).llvm}
	store {types.INT.llvm} %3, {types.Ptr(types.INT).llvm} @ARGC
	store {types.Ptr(types.Array(types.Ptr(types.Array(types.CHAR)))).llvm} %4, {types.Ptr(types.Ptr(types.Array(types.Ptr(types.Array(types.CHAR))))).llvm} @ARGV
"""
		else:
			self.text += f"""
define private {ot.llvm} {name}\
({', '.join(f'{arg.typ.llvm} %argument{arg.uid}' for arg in node.arg_types)}) {{
	%retvar = alloca {ot.llvm}
"""
		self.visit(node.code)

		if node.name.operand == 'main':
			self.text += f"""\
	ret i64 0
}}
"""
			self.names = old
			assert node.arg_types == ()
			assert node.return_type == types.VOID
			return TV()
		self.text += f"""\
	{f'br label %return' if ot == types.VOID else 'unreachable'}
return:
	%retval = load {ot.llvm}, {ot.llvm}* %retvar
	ret {ot.llvm} %retval
}}
"""
		self.names = old
		return TV()
	def visit_code(self, node:nodes.Code) -> TV:
		name_before = self.names.copy()
		for statemnet in node.statements:
			self.visit(statemnet)
		self.names = name_before
		return TV()
	def visit_call(self, node:nodes.Call) -> TV:
		args = [self.visit(arg) for arg in node.args]
		actual_types = [arg.typ for arg in args]
		def get_fun_out_of_called(called:TV) -> tuple[types.Fun, TV]:
			if isinstance(called.typ, types.Fun):
				return called.typ,called
			if isinstance(called.typ, types.BoundFun):
				return called.typ.apparent_typ,called
			if isinstance(called.typ, types.StructKind):
				d = {o:called.typ.generics[idx] for idx,o in enumerate(called.typ.struct.generics)}
				return types.Fun(
					called.typ.struct.get_magic('init', node.loc).typ.arg_types[1:],
					types.Ptr(types.Struct(called.typ.name,called.typ.generics,))
				).fill_generic(d), called
			if isinstance(called.typ, MixTypeTv):
				for ref in called.typ.funs:
					fun,tv = get_fun_out_of_called(ref)
					if len(actual_types) != len(fun.arg_types):
						continue#continue searching
					for actual_arg,arg in zip(actual_types,fun.arg_types,strict=True):
						if actual_arg != arg:
							break#break to continue
					else:
						return fun,tv#found fun
					continue
				assert False
			assert False
		fun_equiv,callable = get_fun_out_of_called(self.visit(node.func))
		assert isinstance(callable.typ,types.Fun|types.BoundFun|types.StructKind)
		return_tv = None
		if isinstance(callable.typ,types.BoundFun):
			fun = TV(callable.typ.fun,callable.val)
			args = [TV(callable.typ.typ,callable.typ.val)] + args
		elif isinstance(callable.typ,types.StructKind):
			struct = callable.typ.struct
			r = struct.get_magic('init', node.loc)
			d = {o:callable.typ.generics[idx] for idx,o in enumerate(struct.generics)}
			return_tv = self.allocate_type_helper(types.Struct(callable.typ.name,callable.typ.generics), node.uid)
			fun = TV(r.typ.fill_generic(d), types.Generic.fill_llvmid(r.llvmid,callable.typ.generics))
			args = [return_tv] + args
		else:
			fun = callable
		assert isinstance(fun.typ,types.Fun)
		self.text+=f"""\
	%callresult{node.uid} = call {fun.typ.return_type.llvm} {fun.val}({', '.join(str(a) for a in args)})
"""
		if return_tv is None:
			return_tv = TV(fun.typ.return_type, f"%callresult{node.uid}")
		return return_tv
	def visit_str(self, node:nodes.Str) -> TV:
		self.strings.append(node)
		l = len(node.token.operand)
		return TV(types.STR,f"<{{i64 {l}, [0 x i8]* bitcast([{l} x i8]* {node.llvmid} to [0 x i8]*)}}>")
	def visit_int(self, node:nodes.Int) -> TV:
		return TV(types.INT, node.token.operand)
	def visit_short(self, node:nodes.Short) -> TV:
		return TV(types.SHORT, node.token.operand)
	def visit_char(self, node:nodes.Char) -> TV:
		return TV(types.CHAR, f"{ord(node.token.operand)}")
	def visit_bin_exp(self, node:nodes.BinaryExpression) -> TV:
		left = self.visit(node.left)
		right = self.visit(node.right)
		lr = left.typ,right.typ
		lv = left.val
		rv = right.val

		op = node.operation
		implementation:None|str = None
		if op.equals(TT.KEYWORD,'and') and lr == (types.BOOL,types.BOOL):
			implementation = f'and {types.BOOL.llvm} {lv}, {rv}'
		elif op.equals(TT.KEYWORD,'or' ) and lr == (types.BOOL,types.BOOL):
			implementation = f'or { types.BOOL.llvm} {lv}, {rv}'
		elif op.equals(TT.KEYWORD,'xor') and lr == (types.BOOL,types.BOOL):
			implementation = f'xor {types.BOOL.llvm} {lv}, {rv}'
		elif (
				(left.typ == right.typ == types.INT  ) or 
				(left.typ == right.typ == types.SHORT) or 
				(left.typ == right.typ == types.CHAR )):
			implementation = {
			TT.PERCENT:             f"srem {left}, {rv}",
			TT.PLUS:                  f"add nsw {left}, {rv}",
			TT.MINUS:                 f"sub nsw {left}, {rv}",
			TT.ASTERISK:              f"mul nsw {left}, {rv}",
			TT.DOUBLE_SLASH:             f"sdiv {left}, {rv}",
			TT.LESS:            f"icmp slt {left}, {rv}",
			TT.LESS_OR_EQUAL:   f"icmp sle {left}, {rv}",
			TT.GREATER:         f"icmp sgt {left}, {rv}",
			TT.GREATER_OR_EQUAL:f"icmp sge {left}, {rv}",
			TT.DOUBLE_EQUALS:    f"icmp eq {left}, {rv}",
			TT.NOT_EQUALS:       f"icmp ne {left}, {rv}",
			TT.DOUBLE_LESS:          f"shl {left}, {rv}",
			TT.DOUBLE_GREATER:      f"ashr {left}, {rv}",
			}.get(node.operation.typ)
			if op.equals(TT.KEYWORD,'xor'):implementation = f'xor {left}, {rv}'
			if op.equals(TT.KEYWORD, 'or'):implementation =  f'or {left}, {rv}'
			if op.equals(TT.KEYWORD,'and'):implementation = f'and {left}, {rv}'
		elif (  isinstance( left.typ,types.Ptr) and
			isinstance(right.typ,types.Ptr) ):
			implementation = {
				TT.DOUBLE_EQUALS:  f"icmp eq {left}, {rv}",
				TT.NOT_EQUALS: f"icmp ne {left}, {rv}",
			}.get(node.operation.typ)
		assert implementation is not None, f"op '{node.operation}' is not implemented yet for {left.typ}, {right.typ} {node.operation.loc}"
		self.text+=f"""\
	%bin_op{node.uid} = {implementation}
"""


		return TV(node.typ(left.typ, right.typ), f"%bin_op{node.uid}")
	def visit_expr_state(self, node:nodes.ExprStatement) -> TV:
		self.visit(node.value)
		return TV()
	def visit_refer(self, node:nodes.ReferTo) -> TV:
		tv = self.names.get(node.name.operand)
		assert tv is not None, f"{node.name.loc} name '{node.name.operand}' is not defined (tc is broken) {node}"
		if isinstance(tv.typ,types.StructKind):
			assert len(tv.typ.struct.generics) == len(node.generics)
			d = {o:node.generics[idx] for idx,o in enumerate(tv.typ.struct.generics)}
			return TV(tv.typ.fill_generic(d),tv.val)
		if isinstance(tv.typ,types.GenericFun):
			assert len(tv.typ.fun.generics) == len(node.generics)
			d = {o:node.generics[idx] for idx,o in enumerate(tv.typ.fun.generics)}
			return TV(tv.typ.fill_generic(d),tv.typ.llvmid(node.generics))
		return tv
	def allocate_type_helper(self, typ:types.Type, uid:int, times:TV|None = None) -> TV:
		if times is None:
			tv = TV(types.Ptr(typ), f"%nv{uid}")
			time = TV(types.INT,'1')
		else:
			tv = TV(types.Ptr(types.Array(typ)), f"%nv{uid}")
			time = times
		if typ == types.VOID:
			return tv
		self.text += f"""\
	%nv1{uid} = getelementptr {typ.llvm}, {types.Ptr(typ).llvm} null, {time}
	%nv2{uid} = ptrtoint {types.Ptr(typ).llvm} %nv1{uid} to i64
	%nv3{uid} = call i8* @GC_malloc(i64 %nv2{uid})
	%nv{uid} = bitcast i8* %nv3{uid} to {tv.typ.llvm}
"""
		return tv
	def visit_declaration(self, node:nodes.Declaration) -> TV:
		time:TV|None = None
		if node.times is not None:
			time = self.visit(node.times)
		self.names[node.var.name.operand] = self.allocate_type_helper(node.var.typ,node.uid, time)
		return TV()
	def visit_assignment(self, node:nodes.Assignment) -> TV:
		val = self.visit(node.value) # get a value to store
		tv = self.allocate_type_helper(val.typ,node.uid)
		self.names[node.var.name.operand] = tv
		if val.typ == types.VOID:
			return TV()
		self.text += f"""\
	store {val}, {tv}
"""
		return TV()
	def visit_save(self, node:nodes.Save) -> TV:
		space = self.visit(node.space)
		value = self.visit(node.value)
		if value.typ == types.VOID:
			return TV()
		self.text += f"""\
	store {value}, {space}
"""
		return TV()
	def store_type_helper(self, space:TV, value:TV) -> None:
		if space.typ == types.VOID:
			return
		self.text += f"\tstore {value}, {space}\n"
	def visit_variable_save(self, node:nodes.VariableSave) -> TV:
		space = self.names.get(node.space.operand)
		value = self.visit(node.value)
		if space is None:
			space = self.allocate_type_helper(value.typ,node.uid)
			self.names[node.space.operand] = space
		self.store_type_helper(space,value)
		return TV()

	def visit_if(self, node:nodes.If) -> TV:
		cond = self.visit(node.condition)
		self.text+=f"""\
	br {cond}, label %ift{node.uid}, label %iff{node.uid}
ift{node.uid}:
"""
		self.visit(node.code)
		self.text+=f"""\
	br label %ife{node.uid}
iff{node.uid}:
"""
		if node.else_code is not None:
			self.visit(node.else_code)
		self.text+=f"""\
	br label %ife{node.uid}
ife{node.uid}:
"""
		return TV()
	def visit_while(self, node:nodes.While) -> TV:
		self.text+=f"""\
	br label %whilec{node.uid}
whilec{node.uid}:
"""
		cond = self.visit(node.condition)
		self.text+=f"""\
	br {cond}, label %whileb{node.uid}, label %whilee{node.uid}
whileb{node.uid}:
"""
		self.visit(node.code)
		self.text+=f"""\
	br label %whilec{node.uid}
whilee{node.uid}:
"""
		return TV()
	def visit_constant(self, node:nodes.Constant) -> TV:
		constants = {
			'False':TV(types.BOOL,'false'),
			'True' :TV(types.BOOL,'true'),
			'Null' :TV(types.Ptr(types.VOID) ,'null'),
			'Argv' :TV(types.Ptr(types.Array(types.Ptr(types.Array(types.CHAR)))) ,f'%Argv{node.uid}'),
			'Argc' :TV(types.INT ,f'%Argc{node.uid}'),
			'Void' :TV(types.VOID),
		}
		if node.name.operand == 'Argv':
			self.text+=f"""\
	%Argv{node.uid} = load {types.Ptr(types.Array(types.Ptr(types.Array(types.CHAR)))).llvm}, {types.Ptr(types.Ptr(types.Array(types.Ptr(types.Array(types.CHAR))))).llvm} @ARGV
"""
		if node.name.operand == 'Argc':
			self.text+=f"""\
	%Argc{node.uid} = load {types.INT.llvm}, {types.Ptr(types.INT).llvm} @ARGC
"""
		implementation = constants.get(node.name.operand)
		assert implementation is not None, f"Constant {node.name} is not implemented yet"
		return implementation
	def visit_unary_exp(self, node:nodes.UnaryExpression) -> TV:
		val = self.visit(node.left)
		l = val.typ
		op = node.operation
		if   op == TT.NOT: i = f'xor {val}, -1'
		elif op == TT.AT:
			assert isinstance(l,types.Ptr), f"{node} {op.loc} {val}"
			if l.pointed == types.VOID:
				return TV(types.VOID)
			i = f'load {node.typ(l).llvm}, {val}'
		else:
			assert False, f"Unreachable, {op = } and {l = }"
		self.text+=f"""\
	%uo{node.uid} = {i}
"""
		return TV(node.typ(l),f"%uo{node.uid}")
	def visit_const(self, node:nodes.Const) -> TV:
		return TV()
	def visit_struct(self, node:nodes.Struct) -> TV:
		for generic_fill in node.generic_fills:
			for idx, generic in enumerate(generic_fill):
				types.Generic.fills[node.generics[idx]] = generic
			for fun in node.funs:
				self.visit_fun(fun, types.Generic.fill_llvmid(fun.llvmid,generic_fill))
		for generic in node.generics:
			assert types.Generic.fills.pop(generic,None) is not None, f"Type checker did not append node.generics to node.generic_fills (as it should be)"
		return TV()
	def visit_mix(self,node:nodes.Mix) -> TV:
		return TV()
	def visit_use(self,node:nodes.Use) -> TV:
		return TV()
	def visit_set(self,node:nodes.Alias) -> TV:
		value = self.visit(node.value)
		self.names[node.name.operand] = value
		return TV()
	def visit_return(self, node:nodes.Return) -> TV:
		rv = self.visit(node.value)
		if rv.typ != types.VOID:
			self.text += f"""\
	store {rv}, {types.Ptr(rv.typ).llvm} %retvar
"""
		self.text+= "	br label %return\n"
		return TV()
	def visit_dot(self, node:nodes.Dot) -> TV:
		origin = self.visit(node.origin)
		if isinstance(origin.typ,types.Module):
			v = self.modules[origin.typ.module.uid].names.get(node.access.operand)
			assert v is not None
			return v
		if isinstance(origin.typ,types.StructKind):
			assert len(origin.typ.generics) == len(origin.typ.struct.generics)
			d = {o:origin.typ.generics[idx] for idx,o in enumerate(origin.typ.struct.generics)}
			idx,typ = node.lookup_struct_kind(origin.typ)
			typ = typ.fill_generic(d)
			self.text += f"""\
	%dot1{node.uid} = getelementptr {origin.typ.llvm}, {TV(types.Ptr(origin.typ),origin.typ.llvmid)}, i32 0, i32 {idx}
	%dot{node.uid} = load {typ.llvm}, {types.Ptr(typ).llvm} %dot1{node.uid}
"""
			return TV(typ,f'%dot{node.uid}')
		assert isinstance(origin.typ,types.Ptr), f'dot lookup is not supported for {origin} yet'
		pointed = origin.typ.pointed
		if isinstance(pointed, types.Struct):
			struct = self.structs[pointed.name]
			r = node.lookup_struct(struct)
			d = {o:pointed.generics[idx] for idx,o in enumerate(struct.generics)}
			if isinstance(r,tuple):
				idx,typ = r
				self.text += f"""\
	%dot{node.uid} = getelementptr {pointed.llvm}, {origin}, i32 0, i32 {idx}
"""
				return TV(types.Ptr(typ.fill_generic(d)),f"%dot{node.uid}")
			return TV(types.BoundFun(r.typ.fill_generic(d), origin.typ, origin.val), types.Generic.fill_llvmid(r.llvmid,pointed.generics))
		else:
			assert False, f'unreachable, unknown {type(origin.typ.pointed) = }'
	def visit_get_item(self, node:nodes.Subscript) -> TV:
		origin = self.visit(node.origin)
		subscript = self.visit(node.subscript)
		assert subscript.typ == types.INT
		if origin.typ == types.STR:
			self.text += f"""\
	%gi1{node.uid} = extractvalue {origin}, 1
	%gi2{node.uid} = getelementptr {types.Array(types.CHAR).llvm}, {types.Ptr(types.Array(types.CHAR)).llvm} %gi1{node.uid}, i64 0, {subscript}
	%gi{node.uid} = load i8, i8* %gi2{node.uid}
"""
			return TV(types.CHAR,f"%gi{node.uid}")
		assert isinstance(origin.typ,types.Ptr), "unreachable"
		pointed = origin.typ.pointed
		if isinstance(pointed, types.Array):
			self.text +=f"""\
	%gi{node.uid} = getelementptr {pointed.llvm}, {origin}, i32 0, {subscript}
"""
			return TV(types.Ptr(pointed.typ),f'%gi{node.uid}')
		if isinstance(pointed, types.Struct):
			struct = self.structs.get(pointed.name)
			assert struct is not None
			fun_node = struct.get_magic('subscript', node.loc)
			d = {o:pointed.generics[idx] for idx,o in enumerate(struct.generics)}
			fun = fun_node.typ.fill_generic(d)
			assert len(fun.arg_types) == 2
			assert fun.arg_types[1] == subscript.typ
			self.text += f"""\
	%gi{node.uid} = call {fun.return_type.llvm} {types.Generic.fill_llvmid(fun_node.llvmid,pointed.generics)}({origin}, {subscript})
"""
			return TV(fun.return_type,f'%gi{node.uid}')
		else:
			assert False, 'unreachable'
	def visit_string_cast(self, node:nodes.StrCast) -> TV:
		length = self.visit(node.length)
		pointer = self.visit(node.pointer)
		assert length.typ == types.INT
		assert pointer.typ == types.Ptr(types.Array(types.CHAR))
		self.text += f"""\
	%scast1{node.uid} = insertvalue {types.STR.llvm} undef, {length}, 0
	%strcast{node.uid} = insertvalue {types.STR.llvm} %scast1{node.uid}, {pointer}, 1
"""
		return TV(types.STR,f"%strcast{node.uid}")
	def visit_cast(self, node:nodes.Cast) -> TV:
		val = self.visit(node.value)
		nt = node.typ
		vt = val.typ
		isptr:Callable[[types.Type],bool] = lambda t: isinstance(t,types.Ptr)

		if   (vt,nt)==(types.STR,types.INT):
			self.text += f"\t%extract{node.uid} = extractvalue {val}, 0\n"
			return TV(nt,f"%extract{node.uid}")
		elif (vt,nt)==(types.STR,types.Ptr(types.Array(types.CHAR))):
			self.text += f"\t%extract{node.uid} = extractvalue {val}, 1\n"
			return TV(nt,f"%extract{node.uid}")
		elif isptr(vt) and isptr(nt)           :op = 'bitcast'
		elif (vt,nt)==(types.BOOL, types.CHAR ):op = 'zext'
		elif (vt,nt)==(types.BOOL, types.SHORT):op = 'zext'
		elif (vt,nt)==(types.BOOL, types.INT  ):op = 'zext'
		elif (vt,nt)==(types.CHAR, types.SHORT):op = 'zext'
		elif (vt,nt)==(types.CHAR, types.INT  ):op = 'zext'
		elif (vt,nt)==(types.SHORT,types.INT  ):op = 'zext'
		elif (vt,nt)==(types.INT,  types.SHORT):op = 'trunc'
		elif (vt,nt)==(types.INT,  types.CHAR ):op = 'trunc'
		elif (vt,nt)==(types.INT,  types.BOOL ):op = 'trunc'
		elif (vt,nt)==(types.SHORT,types.CHAR ):op = 'trunc'
		elif (vt,nt)==(types.SHORT,types.BOOL ):op = 'trunc'
		elif (vt,nt)==(types.CHAR, types.BOOL ):op = 'trunc'
		else:
			assert False, f"cast {vt} -> {nt} is not implemented yet"
		self.text += f"""\
	%cast{node.uid} = {op} {val} to {node.typ.llvm}
"""
		return TV(node.typ,f'%cast{node.uid}')
	def visit(self, node:Node) -> TV:
		if type(node) == nodes.Import           : return self.visit_import          (node)
		if type(node) == nodes.FromImport       : return self.visit_from_import     (node)
		if type(node) == nodes.Fun              : return self.visit_fun             (node)
		if type(node) == nodes.Const            : return self.visit_const           (node)
		if type(node) == nodes.Struct           : return self.visit_struct          (node)
		if type(node) == nodes.Code             : return self.visit_code            (node)
		if type(node) == nodes.Mix              : return self.visit_mix             (node)
		if type(node) == nodes.Use              : return self.visit_use             (node)
		if type(node) == nodes.Call             : return self.visit_call            (node)
		if type(node) == nodes.BinaryExpression : return self.visit_bin_exp         (node)
		if type(node) == nodes.UnaryExpression  : return self.visit_unary_exp       (node)
		if type(node) == nodes.ExprStatement    : return self.visit_expr_state      (node)
		if type(node) == nodes.Assignment       : return self.visit_assignment      (node)
		if type(node) == nodes.ReferTo          : return self.visit_refer           (node)
		if type(node) == nodes.Declaration      : return self.visit_declaration     (node)
		if type(node) == nodes.Save             : return self.visit_save            (node)
		if type(node) == nodes.VariableSave     : return self.visit_variable_save   (node)
		if type(node) == nodes.If               : return self.visit_if              (node)
		if type(node) == nodes.While            : return self.visit_while           (node)
		if type(node) == nodes.Alias            : return self.visit_set             (node)
		if type(node) == nodes.Return           : return self.visit_return          (node)
		if type(node) == nodes.Constant         : return self.visit_constant        (node)
		if type(node) == nodes.Dot              : return self.visit_dot             (node)
		if type(node) == nodes.Subscript        : return self.visit_get_item        (node)
		if type(node) == nodes.Cast             : return self.visit_cast            (node)
		if type(node) == nodes.StrCast          : return self.visit_string_cast     (node)
		if type(node) == nodes.Str              : return self.visit_str             (node)
		if type(node) == nodes.Int              : return self.visit_int             (node)
		if type(node) == nodes.Short            : return self.visit_short           (node)
		if type(node) == nodes.Char             : return self.visit_char            (node)
		assert False, f'Unreachable, unknown {type(node)=} '
	def generate_assembly(self) -> None:
		setup =''
		self.text = f"""
define private void {self.module.llvmid}() {{
"""
		for node in self.module.tops:
			if isinstance(node,nodes.Import):
				self.text+= f"\tcall void {node.module.llvmid}()\n"
				if node.module.path not in imported_modules_paths:
					gen = GenerateAssembly(node.module,self.config)
					setup+=gen.text
					imported_modules_paths[node.module.path] = gen
				else:
					gen = imported_modules_paths[node.module.path]
				self.modules[node.module.uid] = gen
				self.names[node.name] = TV(types.Module(node.module))
			elif isinstance(node,nodes.FromImport):
				self.text+= f"\tcall void {node.module.llvmid}()\n"
				if node.module.path not in imported_modules_paths:
					gen = GenerateAssembly(node.module,self.config)
					setup+=gen.text
					imported_modules_paths[node.module.path] = gen
				else:
					gen = imported_modules_paths[node.module.path]
				self.modules[node.module.uid] = gen
				for name in node.imported_names:
					typ = gen.names.get(name)
					if typ is not None:
						self.names[name] = gen.names[name]
						if isinstance(typ.typ,types.StructKind):
							struct = gen.structs.get(name)
							if struct is not None:
								self.structs[name] = struct
								continue
						continue
			elif isinstance(node,nodes.Fun):
				if len(node.generics) != 0:
					self.names[node.name.operand] = TV(types.GenericFun(node))
				else:
					self.names[node.name.operand] = TV(types.Fun(tuple(arg.typ for arg in node.arg_types), node.return_type),node.llvmid)
			elif isinstance(node,nodes.Const):
				self.names[node.name.operand] = TV(types.INT,f"{node.value}")
			elif isinstance(node,nodes.Struct):
				self.names[node.name.operand] = TV(types.StructKind(node,node.generics))
				self.structs[node.name.operand] = node
				node.generic_fills.add(node.generics)
				for generic_fill in node.generic_fills:
					for idx,generic in enumerate(node.generics):
						types.Generic.fills[generic] = generic_fill[idx]
					sk = types.StructKind(node, generic_fill)
					d = {node.generics[idx]:t for idx,t in enumerate(generic_fill)}
					setup += f"""\
	{types.Struct(node.name.operand, generic_fill).llvm} = type {{{', '.join(var.typ.llvm for var in node.variables)}}}
	{sk.llvm} = type {{{', '.join([i.typ.llvm for i in sk.statics]+[i.typ.llvm for i in node.funs])}}}
	{sk.llvmid} = private global {sk.llvm} undef
"""
					u = f"{'.'.join(f'{generic.llvm} ' for generic in generic_fill)}{node.uid}"
					for idx,i in enumerate(node.static_variables):
						value=self.visit(i.value)
						self.text+=f'''\
		%"v{u}{idx+1}" = insertvalue {sk.llvm} {f'%"v{u}{idx}"' if idx !=0 else 'undef'}, {value}, {idx}
	'''
					l = len(node.static_variables)
					for idx,f in enumerate(node.funs):
						idx+=l
						value = TV(f.typ,types.Generic.fill_llvmid(f.llvmid,generic_fill))
						self.text+=f'''\
		%"v{u}{idx+1}" = insertvalue {sk.llvm} {f'%"v{u}{idx}"' if idx !=0 else 'undef'}, {value}, {idx}
	'''
					l+=len(node.funs)
					if l != 0:
						self.text+=f'\tstore {sk.llvm} %"v{u}{l}", {types.Ptr(sk).llvm} {sk.llvmid}\n'
				for generic in node.generics:
					types.Generic.fills.pop(generic)
			elif isinstance(node,nodes.Mix):
				self.names[node.name.operand] = TV(MixTypeTv([self.visit(fun_ref) for fun_ref in node.funs],node.name.operand))
			elif isinstance(node,nodes.Use):
				self.names[node.name.operand] = TV(types.Fun(node.arg_types,node.return_type),f'@{node.name}')
				setup+=f"declare {node.return_type.llvm} @{node.name}({', '.join(arg.llvm for arg in node.arg_types)})\n"
		self.text+="\tret void\n}"
		text = ''
		if self.module.path == '__main__':
			text += f"""\
; Assembly generated by jararaca compiler github.com/izumrudik/jararaca
@ARGV = private global {types.Ptr(types.Array(types.Ptr(types.Array(types.CHAR)))).llvm} undef
@ARGC = private global {types.INT.llvm} undef
declare void @GC_init()
declare noalias i8* @GC_malloc(i64 noundef)
"""
		for node in self.module.tops:
			self.visit(node)
		for string in self.strings:
			l = len(string.token.operand)
			st = ''.join('\\'+('0'+hex(ord(c))[2:])[-2:] for c in string.token.operand)
			text += f"{string.llvmid} = private constant [{l} x i8] c\"{st}\"\n"
		self.text = text+setup+self.text