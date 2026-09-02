"""变异测试的固定 fixture —— 每个 operator 出现次数已知，总数被
tests/test_mutate_sites.py 钉死（22 个 site）。改这个文件 = 改那份判例。

数字 9 之类出现在这句 docstring 里不算 site：字符串常量不在算子集内。
这个文件只被 parse，从不 import（不匹配 test*.py，也不在任何包里）。
"""


def clamp(value, low, high):
    if value < low:                     # cmp_lt
        return low                      # return_none
    if value > high:                    # cmp_gt
        return high                     # return_none
    return value                        # return_none


def total(items):
    count = 0                           # int_plus1 + int_minus1
    for item in items:
        if item == "skip":              # cmp_eq
            continue                    # loop_flow
        if item is None or count >= 9:  # cmp_is / bool_or / cmp_gte / 9 的 ±1
            break                       # loop_flow
        count = count + 1               # arith_add / 1 的 ±1
    return count                        # return_none


def flag(x):
    return x in (True,)                 # cmp_in / const_bool / return_none


class Thing:
    def __init__(self, x):
        self.x = x

    def __repr__(self):                 # 整个函数体跳过（等价变异体高发区）
        return "Thing(" + str(self.x != 1) + ")"


def log_stuff(logger, n):
    logger.warning("count %d", n + 1)   # logging 调用整棵跳过
    logger.log(10, "delta %d", n - 1)   # 同上（log 属性名）


if __name__ == "__main__":              # main 守卫整棵跳过
    print(total([1, 2]))
