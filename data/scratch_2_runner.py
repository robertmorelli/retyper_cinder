from cinderx.compiler.strict import loader as static_python_loader
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
static_python_loader.install()

from data.scratch2 import empty_list


def main():
    c = empty_list()

if __name__ == "__main__":
    main()