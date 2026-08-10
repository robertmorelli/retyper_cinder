# Links that would be valid

Numbered so the graph builder can cite them.

## High priority

1. edge from an annotation's type to the type of every expression cinderx says it reaches
2. edge from an annotation's type to the type context of every expression cinderx says it constrains
3. union of every annotation cinderx reports in the same component
4. edge from the type of `v` in `x = v` to the type of `x`
5. edge from the type of `T` in `x: T = v` to the type context of `v`
6. edge from the type of a name's binding site to the type of every read of that name, for the bindings cinderx does not resolve
7. edge from the type of `o` to the type of `o.a`
8. edge from the type of `a: T` in a class to the type of `o.a`, which cinderx does not give us because it resolves `o.a` to the assignment in `__init__` instead
9. edge from the type of `c` to the type of `c[i]`, whether it is read or stored into
10. edge from the type of `c` to the type context of `i` in `c[i]`
11. edge from the type of `c` to the type context of each of `a`, `b`, `s` in `c[a:b:s]`
12. edge from the type of `x` in `x += v` to the type context of `v`, the same edge the matching binary operator would make
13. edge from the type of `l` to the type context of `r` in `l + r`, and from the type of `r` to the type context of `l`
14. edge from the type of each side of `a < b < c` to the type context of every other side
15. edge from the type of each arm of `a and b` to the type context of every other arm
16. edge from the type of each arm of `a if c else b` to the type context of the other
17. edge from the type of `f` to the type of the result of `f(a)`
18. edge from the type of a parameter to the type context of every argument passed to it
19. edge from each `__init__` parameter annotation to the type context of the matching argument in a call to that class
20. PARTLY FALSE, AND CURRENTLY LOAD BEARING. The code links the argument of every cinder conversion to the type of the call. That is only true for `box` and `unbox`; `int64(x)`, `cbool(x)`, `double(x)`, `clen(x)`, `Array[T](n)` and `cast(T, x)` yield their own type whatever the argument is. Narrowing it to `box` and `unbox` is the honest link and was measured at 11 extra failures, because marking `int64(x)` dynamic is the only thing currently stopping the patcher from boxing a value the tables still call int64 when it is really an int. Fix the staleness first, then narrow this.
21. edge from a return annotation to the type context of every returned expression
22. edge from a return annotation to the type of the call's result
23. edge from the type of `it` to the type of `t` in `for t in it`
24. union of the annotation on `t` and the annotation on `it` in `for t in it`
25. edge from the type of `it` to the type of `t` in `[e for t in it]`
26. edge from the type of `e` to the type of the whole comprehension in `[e for t in it]`
27. edge from the type of each item of a list, set, or dict literal to the type of the literal
28. union of an attribute annotation in a base class and the same name in every subclass
29. union of a method's parameter annotations and the same parameters in every override
30. union of a method's return annotation and the return annotation in every override
31. union of the values a variable takes on the two paths that rejoin at an `if`, a loop, or a `try` and its `except`, when either is a machine type, because a machine type and dynamic have no common type to settle on where the paths meet
32. an expression returned from an inline function must have a perfect type and type context match
33. an argument passed to an inline function must have a perfect type and type context match
34. `e` must have a perfect type and type context match in `[e for t in it]`
35. a `while` condition has a boolean or machine boolean type context
36. an `if` condition has a boolean or machine boolean type context
37. union of a property getter's return annotation and its setter's value annotation

## Low priority

38. edge from the type of `v` in `a = b = v` to the type of `a`, and a separate edge to the type of `b`
39. edge from the element type of `seq` to the element type of `rest` in `a, *rest = seq`
40. edge from the type of `v` in `x := v` to the type of `x`
41. edge from the type of `v` in `x := v` to the type of the expression `(x := v)` itself
42. edge from the type of a parameter to the type context of the matching keyword argument, of its default value, and of every extra argument collected by `*args` or `**kwargs`
43. edge from an `isinstance(x, T)` test to the type of every read of `x` inside the guarded branch, so the narrowed type reaches those reads by ordinary flow
44. edge from an `x is None` or `x is not None` test to the type of every read of `x` inside the guarded branch, so the narrowed type reaches those reads by ordinary flow
45. edge from a `match` case pattern to the type of every read of the subject inside that case body
46. union of a decorated function's annotations and the decorator's parameter and return annotations
47. edge from the type context of wherever a lambda is used to the type context of its body
48. edge from a declared yield annotation to the type context of every yielded expression
49. edge from what an awaited thing resolves to, to the type of the await expression
50. edge from the type of `it` to the type of `t` in `async for t in it`
51. edge from the type of `k` to the key type and from the type of `v` to the value type in `{k: v for t in it}`
52. `c` has a boolean type context in `[e for t in it if c]`
53. edge from the return annotation of `m.__enter__` to the type of `x` in `with m as x`
54. edge from the annotation in `except E as e` to the type of `e`
55. an `assert` condition has a boolean or machine boolean type context
56. a raised expression has an exception type context
57. a value interpolated into an f-string has a dynamic type context
58. edge from a dunder method's parameter annotations to the type contexts of the operands of the operator that calls it

## Added while building

59. edge from the type of `l` to the type of `l + r`, because the left operand decides the result -- `"" * 2` is a str and `[1] * 2` is a list, whatever the count on the right is
60. NOT VALID -- no edge from `l` to the type of `l < r`. A comparison is a boolean whatever its operands are, so its type does not follow them. Tried it: `self.my_output is not None` got marked dynamic when it is really a cbool, the patcher stopped boxing it, and a cbool landed in a dynamic slot.
61. NOT VALID -- no edge from `x` to the type of `not x`. Same reason as 60. Measured 3 extra failures on its own. A sign change like `-x` does follow its operand, but `not` does not, and they share a visitor.
62. edge from the type of every arm of `a and b` to the type of the whole expression, because the result is one of the arms
63. edge from the type of both arms of `a if c else b` to the type of the whole expression, because the result is one of the arms
64. edge from the type of `o.a` or `c[i]` to the type context of `v` in `o.a = v` and `c[i] = v`, because the slot has a declared type of its own and demands the value -- unlike `x = v`, where the plain name takes its type from the value instead
65. edge from the type of `v` to the type of the declaration in `x: T = v`, because cinderx narrows a declaration to its initializer whether or not the annotation is present -- so erasing `T` does not make `x` dynamic, it makes `x` whatever `v` yields
