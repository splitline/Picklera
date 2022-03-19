# Pickora 🐰

A small compiler that can convert Python scripts to pickle bytecode. 

## Requirements

- Python 3.8+

No third-party modules are required.

## Usage

```
usage: pickora.py [-h] [-f FILE] [-d] [-r] [-l {none,python,pickle}] [-c CODE] [-o OUTPUT]

A toy compiler that can convert Python scripts to pickle bytecode.

optional arguments:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  the Python script to compile
  -d, --dis             disassamble compiled pickle bytecode
  -r, --run             run the compiled pickle bytecode
  -l {none,python,pickle}, --lambda {none,python,pickle}
                        choose lambda compiling mode
  -c CODE, --code CODE  code passed in as a string
  -o OUTPUT, --output OUTPUT
                        write compiled pickle to file

Basic usage: `python pickora.py -f samples/hello.py` or `python pickora.py -c 'print("Hello, world!")'`
```

> Note: Lambda syntax is disabled (`--lambda=none`) by default.

### Quick Example

```sh
$ python3 pickora.py -f samples/hello.py --output output.pkl --dis
    0: \x80 PROTO      4
    2: \x95 FRAME      99
            ...
  108: N    NONE
  109: .    STOP
highest protocol among opcodes = 4

$ python3 -m pickle output.pkl
===================
| Hello, world! 🐱 |
===================
None
```

In this example, we compiled [`samples/hello.py`](./samples/hello.py) to `output.pkl` and show the disassembled result of the compiled pickle bytecode. 

But note that this won't run the pickle for you. If you want to do so, add `-r` option or execute `python -m pickle output.pkl` as in this example.

## Supported Syntax

- Literal: int, float, bytes, string, dict, list, set, tuple, bool, None
- Assignment: `val = dict_['x'] = obj.attr = 'meow'` (directly using bytecode for all of these operation)
- Attributes: `obj.attr` (using `builtins.getattr` only when you need to "load" an attribute)
- Named assignment: `(x := 0xff)`
- Function call: `f(arg1, arg2)`
  - Doesn't support keyword argument.
- Operators (using `operator` module)
  - Binary operators: `+`, `-`, `*`, `/` etc.
  - Unary operators: `not`, `~`, `+val`, `-val`
  - Compare: `0 < 3 > 2 == 2 > 1` (using `builtins.all` for chained comparing)
  - Subscript: `list_[1:3]`, `dict_['key']` (using `builtins.slice` for slice)
  - Boolean operators (using `builtins.next`, `builtins.filter`)
    - and: using `operator.not_`
    - or: using `operator.truth`
    - `(a or b or c)` -> `next(filter(truth, (a, b, c)), c)`
    - `(a and b and c)` -> `next(filter(not_, (a, b, c)), c)`
- Import
  - `import module` (using `builtins.__import__`)
  - `from module import things` (directly using `STACK_GLOBALS` bytecode)
- Lambda
  - `lambda x,y=1: x+y`
  - Using `types.CodeType` and `types.FunctionType`
  - Disabled by default
  - [Known bug] If any global variables are changed after the lambda definition, the lambda function won't see those changes.


## Special Syntax

### `RETURN`

`RETURN` is a keyword reserved for specifying `pickle.load(s)` result. This keyword should only be put in the last statement alone, and you can assign any value / expression to it. 

For example, after you compile the following code and use `pickle.loads` to load the compiled pickle, it returns a string `'INT_MAX=2147483647'`.
```python
# source.py
n = pow(2, 31) - 1
RETURN = "INT_MAX=%d" % n
```
It might look like this:
```shell
$ python3 pickora.py -f source.py -o output.pkl
Saving pickle to output.pkl

$ python3 -m pickle output.pkl
'INT_MAX=2147483647'
```
### Macros

There are currently 3 macros available: `STACK_GLOBAL`, `GLOBAL` and `INST`.

#### `STACK_GLOBAL(modname: Any, name: Any)`

Example:
```python
function_name = input("> ") # > system
func = STACK_GLOBAL('os', function_name) # <built-in function system>
func("date") # Tue Jan 13 33:33:37 UTC 2077
```

Behaviour:
1. PUSH modname
2. PUSH name
3. STACK_GLOBAL

#### `GLOBAL(modname: str, name: str)`

Example:
```python
func = GLOBAL("os", "system") # <built-in function system>
func("date") # Tue Jan 13 33:33:37 UTC 2077
```

Behaviour:

Simply run this piece of bytecode: `f"c{modname}\n{name}\n"`

#### `INST(modname: str, name: str, args: tuple[Any])`

Example:
```python
command = input("cmd> ") # cmd> date
INST("os", "system", (command,)) # Tue Jan 13 33:33:37 UTC 2077
```

Behaviour:
1. PUSH a MARK
2. PUSH `args` by order
3. Run this piece of bytecode: `f'i{modname}\n{name}\n'`

## Todos

- [x] Operators (<s>compare</s>, <s>unary</s>, <s>binary</s>, <s>subscript</s>)
- [ ] Unpacking assignment
- [ ] Augmented assignment
- [x] Macros
- [x] Lambda (I don't want to support normal function, because it seems not "picklic" for me)
  - [x] Python bytecode mode
  - [ ] Pickle bytecode mode

### Impracticable 
- [ ] Function call with kwargs
  - `NEWOBJ_EX` only support type object (it calls `__new__`)

## FAQ

### What is pickle?

[RTFM](https://docs.python.org/3/library/pickle.html).

### Why?

It's cool.

### Is it useful?

No, not at all, it's definitely useless.

### So, is this garbage?

Yep, it's cool garbage.

### Would it support syntaxes like `if` / `while` / `for` ?

No. All pickle can do is just simply define a variable or call a function, so this kind of syntax wouldn't exist.

But if you want to do things like:
```python
ans = input("Yes/No: ")
if ans == 'Yes':
  print("Great!")
elif ans == 'No':
  exit()
```
It's still achievable! You can rewrite your code like this:

```python
from functools import partial
condition = {'Yes': partial(print, 'Great!'), 'No': exit}
ans = input("Yes/No: ")
condition.get(ans, repr)()
```
ta-da!

For the loop syntax, you can try to use `map` / `starmap` /  `reduce` etc .

And yes, you are right, it's functional programming time!

