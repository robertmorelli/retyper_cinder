import __static__

if __name__ == '__main__':
    import cinderx.jit
    from cinderx.compiler.strict.loader import install
    cinderx.jit.compile_after_n_calls(0)
    install()
    import wtf2


def bump() -> int:
    global counter
    counter = 1
    return counter


def drive() -> None:
    bump()


counter = None
drive()
