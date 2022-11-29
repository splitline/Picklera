import ast
import sys
import pickle
import pickletools
import types
from helper import *

PICKLE_RETURN_KEY = 'RETURN'


class Compiler:
    def __init__(self, /, filename='<pickora>', source='', compile_lambda='none'):
        self.filename = filename
        if filename == '<pickora>' and source != '':
            self.source = source
        else:
            self.source = open(filename, 'r').read()
        self.compile_lambda = compile_lambda
        self.bytecode = bytes()
        self.memo_manager = MemoManager()

    def compile(self):
        tree = ast.parse(self.source)
        if __import__('os').getenv("DEBUG"):
            kwargs = {'indent': 4} if sys.version_info >= (3, 9) else {}
            print(ast.dump(tree, **kwargs))

        self.bytecode += pickle.PROTO + b"\x04"  # protocol 4

        if len(tree.body) == 0:
            self.bytecode += pickle.NONE + pickle.STOP
            return self.bytecode

        self.bytecode += pickle.MARK  # for POP_MARK
        for node in tree.body[:-1]:
            self.traverse(node)

        self.traverse(tree.body[-1], last=True)

        self.bytecode = pickletools.optimize(self.bytecode)
        return self.bytecode

    def find_class(self, modname, name):
        if self.memo_manager.contains((modname, name)):
            self.fetch_memo((modname, name))
        else:
            # self.bytecode += f'c{modname}\n{name}\n'.encode()
            self.traverse(ast.Constant(value=modname))
            self.traverse(ast.Constant(value=name))
            self.bytecode += pickle.STACK_GLOBAL

            # cache imported function / class to memo
            # if it is only used once, this bytecode will be removed by `pickletools.optimize` later
            self.put_memo((modname, name))

    def fetch_memo(self, key):
        index = self.memo_manager.get_memo(key).index
        if index <= 0xff:
            self.bytecode += pickle.BINGET + index.to_bytes(1, 'little')
        else:
            self.bytecode += pickle.LONG_BINGET + index.to_bytes(4, 'little')

    def put_memo(self, name):
        index = self.memo_manager.get_memo(name).index
        if self.memo_manager.contains(name):
            if index <= 0xff:
                self.bytecode += pickle.BINPUT + index.to_bytes(1, 'little')
            else:
                self.bytecode += pickle.LONG_BINPUT + index.to_bytes(4, 'little')
        else:
            self.bytecode += pickle.MEMOIZE
        # self.bytecode += pickle.PUT + str(index).encode() + b'\n'

    def get_tuple_code(self, size):
        return pickle._tuplesize2code[size] if size <= 3 else pickle.TUPLE

    def call_function(self, func, args):
        macro_handler = dict()

        def macro_build(args):
            assert(len(args) == 2)
            self.traverse(args[0])
            self.traverse(args[1])
            self.bytecode += pickle.BUILD
        macro_handler['BUILD'] = macro_build

        def macro_stack_global(args):
            assert(len(args) == 2)
            self.traverse(args[0])
            self.traverse(args[1])
            self.bytecode += pickle.STACK_GLOBAL
        macro_handler['STACK_GLOBAL'] = macro_stack_global

        def macro_global(args):
            assert(len(args) == 2)
            for i in range(2):
                if not isinstance(args[i], ast.Constant):
                    raise PickoraError(
                        "arguments for GLOBAL macro should be constant", args[i], self.source)
            self.bytecode += f'c{args[0].value}\n{args[1].value}\n'.encode()
        macro_handler['GLOBAL'] = macro_global

        def macro_instance(args):
            assert(len(args) == 3)
            for i in range(2):
                if not isinstance(args[i], ast.Constant):
                    raise PickoraError(
                        "'modname' and 'name' arguments for INST macro should be constant", args[i], self.source)
            self.bytecode += pickle.MARK
            if not isinstance(args[2], ast.Tuple):
                raise PickoraError(
                    "'args' arguments for INST macro should be constant", args[2], self.source)
            for arg in args[2].elts:
                self.traverse(arg)
            self.bytecode += f'i{args[0].value}\n{args[1].value}\n'.encode()
        macro_handler['INST'] = macro_instance

        if type(func) == tuple:
            self.find_class(*func)
        else:
            if type(func) == str:
                func = ast.Name(id=func, ctx=ast.Load())
            if isinstance(func, ast.Name) and macro_handler.get(func.id):
                macro_handler[func.id](args)
                return
            self.traverse(func)
        if len(args) > 3:
            self.bytecode += pickle.MARK
        for arg in args:
            if not isinstance(arg, ast.AST):
                arg = ast.Constant(value=arg)
            self.traverse(arg)
        self.bytecode += self.get_tuple_code(len(args))
        self.bytecode += pickle.REDUCE

    def check_name(self, name, node):
        if name == PICKLE_RETURN_KEY:
            raise PickoraNameError(
                f"Name '{PICKLE_RETURN_KEY}' is reserved for specifying pickle.loads result. "
                "It should be put in the last statement alone.", node, self.source)

    def traverse(self, node, last=False):
        node_parsers = dict()

        def parse_Assign(node):
            targets, value = node.targets, node.value

            # Got a RETURN keyword!
            if last and len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id == PICKLE_RETURN_KEY:
                self.bytecode += pickle.POP_MARK  # cleanup stack
                self.traverse(value)  # put return value onto the stack
                self.bytecode += pickle.STOP  # end of pickle
                return

            def get_assign_value():
                self.bytecode += pickle.BINGET + ASSIGNMENT_TEMP_MEMO.to_bytes(1, 'little')
            # put assignment value to memo
            self.traverse(value)
            self.bytecode += pickle.BINPUT + ASSIGNMENT_TEMP_MEMO.to_bytes(1, 'little')

            for target in targets:
                # TODO: unpacking assignment
                target_type = type(target)
                if target_type == ast.Name:
                    self.check_name(target.id, target)
                    get_assign_value()
                    self.put_memo(target.id)
                elif target_type == ast.Subscript:
                    # For `ITER[IDX] = NEW_VAL`:
                    self.traverse(target.value)  # get ITER
                    self.traverse(target.slice)  # IDX
                    get_assign_value()
                    self.bytecode += pickle.SETITEM
                elif target_type == ast.Attribute:
                    # For `OBJ.ATTR = VAL`:
                    self.traverse(target.value)  # get OBJ
                    # TBD: 
                    # if using __dict__ -> one dict
                    # if using setattr -> tuple: (__dict__, ATTR)

                    # BUILD arg 1: {}
                    self.bytecode += pickle.EMPTY_DICT

                    # BUILD arg 2: {attr: val}
                    self.bytecode += pickle.MARK
                    self.traverse(ast.Constant(target.attr))  # ATTR
                    get_assign_value() # VAL
                    self.bytecode += pickle.DICT

                    self.bytecode += pickle.TUPLE2 + pickle.BUILD
                else:
                    raise PickoraNotImplementedError(
                        f"{type(target).__name__} assignment", node, self.source)

        node_parsers[ast.Assign] = parse_Assign

        def parse_Name(node):
            self.check_name(node.id, node)
            if self.memo_manager.contains(node.id):
                self.fetch_memo(node.id)
            elif is_builtins(node.id):
                self.find_class('builtins', node.id)
            else:
                raise PickoraNameError(f"name '{node.id}' is not defined.", node, self.source)

        node_parsers[ast.Name] = parse_Name

        def parse_Expr(node):
            self.traverse(node.value)

        node_parsers[ast.Expr] = parse_Expr

        def parse_NamedExpr(node):
            self.traverse(node.value)
            self.put_memo(node.target.id)

        node_parsers[ast.NamedExpr] = parse_NamedExpr

        def parse_Call(node):
            self.call_function(node.func, node.args)

        node_parsers[ast.Call] = parse_Call

        def parse_Constant(node):
            val = node.value
            const_type = type(val)

            if const_type == int:
                if 0 <= val <= 0xff:
                    self.bytecode += pickle.BININT1 + val.to_bytes(1, 'little')
                elif 0 <= val <= 0xffff:
                    self.bytecode += pickle.BININT2 + val.to_bytes(2, 'little')
                elif -0x80000000 <= val <= 0x7fffffff:
                    self.bytecode += pickle.BININT + val.to_bytes(4, 'little', signed=True)
                else:
                    self.bytecode += pickle.INT + str(val).encode() + b'\n'
            elif const_type == float:
                self.bytecode += pickle.FLOAT + str(val).encode() + b'\n'
            elif const_type == bool:
                self.bytecode += pickle.NEWTRUE if val else pickle.NEWFALSE
            elif const_type == str:
                encoded = val.encode('utf-8', 'surrogatepass')
                n = len(encoded)
                self.bytecode += (pickle.SHORT_BINUNICODE + n.to_bytes(1, 'little')
                                  if n <= 0xff
                                  else pickle.BINUNICODE + n.to_bytes(4, 'little')) + encoded
            elif const_type == bytes:
                n = len(val)
                self.bytecode += (pickle.SHORT_BINBYTES + n.to_bytes(1, 'little')
                                  if n <= 0xff
                                  else pickle.BINBYTES + n.to_bytes(4, 'little')) + node.value
            elif val == None:
                self.bytecode += pickle.NONE
            elif val == Ellipsis:
                self.find_class('builtins', 'Ellipsis')
            else:
                # I am not sure if there are types I didn't implement 🤔
                raise PickoraNotImplementedError("Type " + repr(const_type), node, self.source)

        node_parsers[ast.Constant] = parse_Constant

        def parse_Tuple(node):
            tuple_size = len(node.elts)
            if tuple_size > 3:
                self.bytecode += pickle.MARK
            for element in node.elts:
                self.traverse(element)
            self.bytecode += self.get_tuple_code(tuple_size)

        node_parsers[ast.Tuple] = parse_Tuple

        def parse_List(node):
            self.bytecode += pickle.MARK
            for element in node.elts:
                self.traverse(element)
            self.bytecode += pickle.LIST

        node_parsers[ast.List] = parse_List

        def parse_Dict(node):
            self.bytecode += pickle.MARK
            assert(len(node.keys) == len(node.values))
            for key, val in zip(node.keys, node.values):
                self.traverse(key)
                self.traverse(val)
            self.bytecode += pickle.DICT

        node_parsers[ast.Dict] = parse_Dict

        def parse_Set(node):
            self.bytecode += pickle.EMPTY_SET
            self.bytecode += pickle.MARK
            for element in node.elts:
                self.traverse(element)
            self.bytecode += pickle.ADDITEMS

        node_parsers[ast.Set] = parse_Set

        def parse_Compare(node):
            # a > b > c -> all((a > b, b > c))
            if len(node.ops) == 1:
                op = node.ops[0]
                self.call_function(
                    func=('operator', op_to_method.get(type(op))),
                    args=[node.left, node.comparators[0]]
                )
                return

            left = node.left
            func = CallAst(
                func=("builtins", 'all'),
                args=[tuple(
                    CallAst(
                        func=('operator', op_to_method.get(type(op))),
                        args=[left, left := right]
                    )
                    for op, right in zip(node.ops, node.comparators)
                )]
            )
            self.traverse(func)

        node_parsers[ast.Compare] = parse_Compare

        def parse_BoolOp(node):
            # (a or b or c)     next(filter(truth, (a, b, c)), c)
            # (a and b and c)   next(filter(not_, (a, b, c)), c)
            _get_bool_func = {ast.Or: 'truth', ast.And: 'not_'}

            self.call_function(('builtins', 'next'), (
                CallAst(func=('builtins', 'filter'),
                        args=[CallAst(func='STACK_GLOBAL',
                                      args=['operator', _get_bool_func[type(node.op)]]),
                              node.values]),
                node.values[-1]
            ))

        node_parsers[ast.BoolOp] = parse_BoolOp

        # [ast.BinOp, ast.UnaryOp]:
        def _parse_Op(node):
            op = type(node.op)
            assert(op in op_to_method)

            args = tuple()
            if type(node) == ast.BinOp:
                args = (node.left, node.right)
            elif type(node) == ast.UnaryOp:
                args = (node.operand,)

            self.call_function(("operator", op_to_method.get(op)), args)

        node_parsers[ast.BinOp] = node_parsers[ast.UnaryOp] = _parse_Op

        def parse_Subscript(node):
            self.call_function(('operator', "getitem"), (node.value, node.slice))

        node_parsers[ast.Subscript] = parse_Subscript

        # compatible with Python 3.8
        def parse_Index(node):
            self.traverse(node.value)

        node_parsers[ast.Index] = parse_Index

        def parse_Slice(node):
            self.call_function(('builtins', 'slice'), (node.lower, node.upper, node.step))

        node_parsers[ast.Slice] = parse_Slice

        def parse_Attribute(node):
            self.call_function(('builtins', 'getattr'), (node.value, node.attr))

        node_parsers[ast.Attribute] = parse_Attribute

        def parse_ImportFrom(node):
            for alias in node.names:
                if alias.name == '*':
                    raise PickoraNotImplementedError(
                        "doesn't support import * syntax", node, self.source)
                self.find_class(node.module, alias.name)
                if getattr(alias, 'asname') != None:
                    self.check_name(alias.asname, node)
                    self.put_memo(alias.asname)
                else:
                    self.put_memo(alias.name)

        node_parsers[ast.ImportFrom] = parse_ImportFrom

        def parse_Import(node):
            for alias in node.names:
                self.call_function(('builtins', '__import__'), (alias.name, ))
                if getattr(alias, 'asname') != None:
                    self.check_name(alias.asname, node)
                    self.put_memo(alias.asname)
                else:
                    self.put_memo(alias.name)

        node_parsers[ast.Import] = parse_Import

        def parse_Lambda(node):
            if self.compile_lambda == 'none':
                raise PickoraError(
                    'lambda compiling is disabled by default, add `--lambda` option to enable it.', node, self.source)
            elif self.compile_lambda == 'python':
                # Python bytecode mode:
                # convert lambda function to FunctionType(CodeType(...), ...)
                code = compile(ast.Expression(body=node), '<pickora.lambda>', 'eval')
                lambda_code = next(c for c in code.co_consts if type(c) == types.CodeType)

                code_attrs = ('argcount', 'posonlyargcount', 'kwonlyargcount', 'nlocals', 'stacksize', 'flags',
                              'code', 'consts', 'names', 'varnames', 'filename', 'name', 'firstlineno', 'lnotab')
                code_args = [getattr(lambda_code, f'co_{attr}') for attr in code_attrs]

                # it's too complicated to call this constructor manually lol
                call_CodeType = ast.parse(f'CodeType{tuple(code_args)}', mode='eval').body
                call_CodeType.func = ('types', 'CodeType')

                '''
                FIXME (if possible)
                Note that the globals is just a "clone" of current context.
                So if any global variables change after the lambda definition,
                the lambda won't see those changes
                For example:
                    val = 'before'
                    f = lambda:val
                    val = 'after'
                    print(f())  # before
                '''
                func_globals = ast.Dict(keys=[], values=[])
                for name in code_args[8]:  # co_names
                    func_globals.keys.append(ast.Constant(value=name))
                    func_globals.values.append(ast.Name(id=name))

                self.call_function(('types', 'FunctionType'), (
                    call_CodeType,
                    func_globals,
                    None,
                    ast.Tuple(elts=node.args.defaults)
                ))
            else:
                raise PickoraNotImplementedError(
                    f"Not implemented mode '{self.compile_lambda}' for compiling lambda.", node, self.source)

        node_parsers[ast.Lambda] = parse_Lambda

        if isinstance(node, ast.AST):
            if node_parsers.get(type(node)):
                node_parsers[type(node)](node)
            else:
                raise PickoraNotImplementedError(type(node).__name__ + " syntax", node, self.source)

            if last:
                # POP_MARK: clean up stack
                # end of pickle, return None
                self.bytecode += pickle.POP_MARK + pickle.NONE + pickle.STOP
        else:
            raise PickoraNotImplementedError(type(node).__name__ + " syntax", node, self.source)
