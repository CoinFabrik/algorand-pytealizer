from inspect import getsource, getfile
from ast import *

class TealGatherVariables(NodeVisitor):
    def do_visit(self, node):
        self.globals = set()
        self.locals = set()
        self.visit(node)
        return {
            "global": self.globals,
            "scratch": self.locals
        }

    def visit_Assign(self, node):
        for target in node.targets:
            if isinstance(target, Name) and target.id not in self.globals:
                self.locals.add(target.id)

    def visit_Global(self, node):
        for name in node.names:
            if name in self.locals:
                self.locals.remove(name)
            self.globals.add(name)

class TealTransform(NodeTransformer):

    def __init__(self, variables):
       self.variables = variables 

    def do_visit(self, node):
        assert isinstance(node, Module), \
            "Main node node should be a module with a single FunctionDef statement"
        assert len(node.body), \
            "Main node should be a module with a single FunctionDef statement"
        assert isinstance(node.body[0], FunctionDef), \
            "Main node node should be a module with a single FunctionDef statement"
        node = node.body[0]

        # FIXME: Do type inference for ScratchVar types
        variable_type = Attribute(
                Name('TealType', Load()),
                'anytype',
                Load()
        )
        scratch_init = [
                Assign(
                    targets=[Name(id, Store())],
                    value=self.call('ScratchVar', variable_type)
                )
                for id in self.variables['scratch']
        ]
        # FIXME: Maybe we want an implicit reject at the end?
        last_statement = Raise(exc=Name('Approve', Load()), cause=None)
        program = Return(self.stmt_list_to_seq(node.body + [last_statement]))
        body = scratch_init + [program]
        decorator_list = [
                decorator for decorator in node.decorator_list
                if not (isinstance(decorator, Name) and decorator.id == 'Pytealize')
        ]
        return Module(
                body=[
                    FunctionDef(
                            name=node.name,
                            args=node.args,
                            body=body,
                            decorator_list=decorator_list,
                    )
                ],
                type_ignores=[]
        )

    def visit_Constant(self, node):
        constant_type = type(node.value)
        if constant_type is int:
            return self.call('Int', node)
        elif constant_type is str:
            return self.call('Bytes', node)
        raise ValueError('Unexpected constant with type %s' % constant_type)

    def visit_FunctionDef(self, node):
        raise ValueError('Subprocedures are unimplemented')

    def visit_If(self, node):
        test = self.visit(node.test)
        body = self.stmt_list_to_seq(node.body)

        result = self.call('If', test)
        result = self.method_call(result, 'Then', body)

        while len(node.orelse) == 1 and isinstance(node.orelse[0], If):
            node = node.orelse[0]
            test = self.visit(node.test)
            body = self.stmt_list_to_seq(node.body)

            result = self.method_call(result, 'ElseIf', test)
            result = self.method_call(result, 'Then', body)

        if len(node.orelse) != 0:
            else_body = self.stmt_list_to_seq(node.orelse)
            result = self.method_call(result, 'Else', else_body)

        return result

    def visit_Attribute(self, node):
        if self.is_account_store(node.value) and isinstance(node.ctx, Load):
            # App.localGet(<value>, Bytes("<attr>")),
            return self.method_call(
                Name('App', Load()),
                'localGet',
                self.get_account_from_store_reference(node.value),
                self.call('Bytes', Constant(node.attr))
            )
        return node

    def visit_Return(self, node):
        value = self.visit(node.value)
        return self.call('Return', value)

    def visit_Assert(self, node):
        test = self.visit(node.test)
        return self.call('Assert', test)

    def visit_Expr(self, node):
        value = self.visit(node.value)
        return self.call('Pop', value)

    def visit_Assign(self, node):
        assert len(node.targets) == 1, 'Only assignment to single targets is supported'
        target = node.targets[0]
        if isinstance(target, Name):
            if target.id in self.variables['scratch']:
                # <name>.store(<value>)
                target = Name(target.id, Load())
                value = self.visit(node.value)
                return self.method_call(target, 'store', value)
            elif target.id in self.variables['global']:
                # App.globalPut(Bytes("<name>"), <value>)
                target = self.call('Bytes', Constant(target.id))
                value = self.visit(node.value)
                return self.method_call(Name('App', Load()), 'globalPut', target, value)
        elif isinstance(target, Attribute) and self.is_account_store(target.value):
            # App.localPut(<target-index>, Bytes("<attr>"), <value>)
            account = self.get_account_from_store_reference(target.value)
            key = self.call('Bytes', Constant(target.attr))
            value = self.visit(node.value)
            return self.method_call(Name('App', Load()), 'localPut', account, key, value)
        else:
            raise Error('Only assignment to scratch, global or account-local variables is supported')

    def visit_Name(self, node):
        if isinstance(node.ctx, Load) and node.id in self.variables['scratch']:
            # <name>.load()
            return self.method_call(node, 'load')
        if isinstance(node.ctx, Load) and node.id in self.variables['global']:
            # App.globalGet(Bytes("<name>"))
            target = self.call('Bytes', Constant(node.id))
            return self.method_call(Name('App', Load()), 'globalGet', target)
        return node

    def visit_Raise(self, node):
        assert node.cause is None, "`raise x from y` is not supported"
        assert isinstance(node.exc, Name) and node.exc.id in {'Approve', 'Reject'}, \
                "Only `raise Approve` and `raise Reject` are supported"
        return self.call(node.exc.id)

    def stmt_list_to_seq(self, statements):
        filtered_statement_types = {Global}
        statements = [self.visit(s) for s in statements if type(s) not in filtered_statement_types]

        if len(statements) == 1:
            return statements[0]
        return self.call('Seq', *statements)

    def call(self, name, *args):
        return Call(
                func=Name(name, Load()),
                args=list(args),
                keywords=[]
        )

    def method_call(self, value, method_name, *args):
        return Call(
                func=Attribute(value, method_name, Load()),
                args=list(args),
                keywords=[]
        )

    def is_account_store(self, node):
        # <expr>.store
        if isinstance(node, Attribute) and node.attr == 'store':
            return self.is_txn_dot_accounts_subscript(node.value) \
                or self.is_txn_dot_sender_call(node.value)
        return False

    def is_txn_dot_accounts_subscript(self, node):
        # Txn.accounts[<expr>]
        return (
               isinstance(node, Subscript)
           and isinstance(node.slice, Index)
           and isinstance(node.value, Attribute)
           and node.value.attr == 'accounts'
           and isinstance(node.value.value, Name)
           and node.value.value.id == 'Txn'
        )

    def is_txn_dot_sender_call(self, node):
        # Txn.sender()
        return (
               isinstance(node, Call)
           and isinstance(node.func, Attribute)
           and node.func.attr == 'sender'
           and isinstance(node.func.value, Name)
           and node.func.value.id == 'Txn'
        )

    def get_account_from_store_reference(self, node):
        if self.is_txn_dot_accounts_subscript(node.value):
            return self.visit(node.value.slice.value)
        elif self.is_txn_dot_sender_call(node.value):
            return self.call('Int', Constant(0))
        else:
            raise ValueError('Unsupported store reference')

def Pytealize(fn):
    fn_source = getsource(fn)
    fn_ast = parse(fn_source, getfile(fn))

    variables = TealGatherVariables().do_visit(fn_ast)
    new_fn_ast = TealTransform(variables).do_visit(fn_ast)
    fix_missing_locations(new_fn_ast)

    evaluation_locals = {}

    exec(compile(new_fn_ast, filename="<pytealizer>", mode="exec"), globals(), evaluation_locals)
    output_values = list(evaluation_locals.values())
    assert len(output_values) == 1, "Expected only one local declaration when pytealizing a function"

    new_fn = output_values[0]
    setattr(new_fn, "original_fn", fn)
    setattr(new_fn, "original_fn_source", fn_source)
    setattr(new_fn, "original_fn_ast", fn_ast)
    setattr(new_fn, "ast", new_fn_ast)
    return new_fn

def NoPytealize(thing):
    """
    This is just a marker. It can be used to say that some subroutines should
    not be pytealized or to demarcate small pieces of code with "please no
    pytealization here".
    """
    return thing

