from pytealize import Pytealize, NoPytealize

def unparse(ast):
    from astor.code_gen import to_source
    from black import format_file_contents, FileMode, NothingChanged

    source = to_source(ast)
    try:
        return format_file_contents(source, fast=False, mode=FileMode())
    except NothingChanged:
        return source

def print_transform(fn):
    print("=== Original ===")
    print(fn.original_fn_source)
    print("=== Pytealized ===")
    print(unparse(fn.ast))

@Pytealize
def test_transform():
    assert jugador.estado != "baneado"
    if play == opponent_play:
        scratch = 2
    elif (play + 1) % 3 == opponent_play:
        scratch = 1
    elif (opponent_play + 1) % 3 == play:
        return 0
    return scratch

print_transform(test_transform)

@Pytealize
def counter_approval():
    global owner          # byteslice
    global global_counter # uint64

    if Txn.application_id() == 0:
        owner = Txn.sender()
        global_counter = 0
    elif Txn.on_completion() == OnComplete.NoOp:
        if Txn.application_args[0] == "inc":
            scratch_counter = global_counter
            # check overflow
            if scratch_counter < 0xFFFFFFFFFFFFFFFF:
                global_counter = scratch_counter + 1
        elif Txn.application_args[0] == "dec":
            scratch_counter = global_counter
            # check underflow
            if scratch_counter > 0:
                global_counter = scratch_counter - 1
    else:
        raise Reject # Unrecognized transaction

print_transform(counter_approval)

@Pytealize
def message_store_approval():
    if Txn.application_id() == 0:
        Txn.sender().store.message = "hola!"
    elif Txn.on_completion() == OnComplete.NoOp:
        if Txn.application_args[0] == "change":
            assert Txn.application_args[2] == Txn.accounts[0].store.message
            Txn.sender().store.message = Txn.application_args[1]
        elif Txn.application_args[0] == "check":
            assert Txn.application_args[1] == Txn.sender().store.message
    else:
        raise Reject # Unrecognized transaction

print_transform(message_store_approval)
