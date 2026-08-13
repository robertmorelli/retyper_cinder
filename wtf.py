import __static__
from __static__ import double

if __name__ == '__main__':
    import cinderx.jit
    from cinderx.compiler.strict.loader import install
    cinderx.jit.compile_after_n_calls(0)
    install()
    import wtf
    wtf.main()


def f() -> None:
    for i in range(0):
        m: double = 0.0


def main() -> None:
    f()
