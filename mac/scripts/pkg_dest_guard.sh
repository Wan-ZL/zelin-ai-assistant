#!/bin/bash
# CONTRACT §74 —— 「一个 .pkg 永不写进 git checkout」的唯一实现。
#
# 用法：bash mac/scripts/pkg_dest_guard.sh <destination>
# 退出码：
#   0  安全：<destination> 解析之后不在任何 git 工作树里，调用者可以照常写
#   3  拒绝：<destination> 自己或它解析之后的某个祖先带着 .git（stdout 打出
#      那棵 checkout 的根，stderr 打出给人看的说明与修法）
#   2  用法错误（没给参数 / 给了空串）
#
# 两个调用者（都 fail-closed：拿不到 0 就当拒绝）：
#   - mac/package.sh 写进 .pkg 的 postinstall —— rsync 之前问一次；
#   - install.sh 的 --pkg-postinstall —— 第二把锁（§74.2）。
#
# 判例（2026-09-07 live 事故，issue #333）：退役的 Sparkle 壳自动装了 v1.0.14
# 的 .pkg，postinstall 顺着 `~/Projects`（一条指向 /Volumes/Storage 上 live
# checkout 的符号链接）把 master 拷贝 `rsync -a` 进去：232 个 tracked 文件被
# 改写成**更旧**的 tag 的字节、18 个上游早已删掉的源码文件作为 untracked 落
# 回来、actd 被重启并把过期代码钉进内存，自动部署从此 `refused_dirty`。所以
# 判据不是「版本谁新」而是「这是不是一棵工作树」——开发者的 checkout 永远不
# 是合法的安装目标。
set -u

PROG="pkg_dest_guard"

if [ "$#" -ne 1 ] || [ -z "${1:-}" ]; then
    echo "usage: bash pkg_dest_guard.sh <destination-path>" >&2
    exit 2
fi
DEST="$1"

# 物理路径：把路径里每一条符号链接都解开（`~/Projects -> /Volumes/…` 正是事故
# 的入口），不存在的尾巴原样接回去——「还没建出来的目的地」也要能判。
# `cd … && pwd -P` 是可移植的 realpath：macOS 的 /usr/bin/realpath 来得很晚，
# 而 postinstall 跑在一条最小 PATH 上。
resolve_physical() {
    _rp_path="$1"
    _rp_rest=""
    while [ -n "$_rp_path" ]; do
        if [ -d "$_rp_path" ]; then
            printf '%s%s' "$( cd "$_rp_path" && pwd -P )" "$_rp_rest"
            return 0
        fi
        [ "$_rp_path" = "/" ] && break
        _rp_rest="/$(basename "$_rp_path")$_rp_rest"
        _rp_parent="$(dirname "$_rp_path")"
        [ "$_rp_parent" = "$_rp_path" ] && break
        _rp_path="$_rp_parent"
    done
    printf '/%s' "${_rp_rest#/}"
}

# 自己往上走到 /：第一个带 .git 的目录就是那棵 checkout 的根。`.git` 是目录
# （普通 clone）还是文件（worktree / submodule 的 gitdir 指针）都算。
enclosing_checkout() {
    _ec_dir="$1"
    while : ; do
        if [ -e "$_ec_dir/.git" ]; then
            printf '%s' "$_ec_dir"
            return 0
        fi
        [ "$_ec_dir" = "/" ] && return 1
        _ec_parent="$(dirname "$_ec_dir")"
        [ "$_ec_parent" = "$_ec_dir" ] && return 1
        _ec_dir="$_ec_parent"
    done
}

PHYSICAL="$(resolve_physical "$DEST")"

if CHECKOUT="$(enclosing_checkout "$PHYSICAL")"; then
    printf '%s\n' "$CHECKOUT"
    echo "$PROG: refusing to write into $DEST" >&2
    if [ "$PHYSICAL" != "$DEST" ]; then
        echo "$PROG: it resolves to $PHYSICAL" >&2
    fi
    echo "$PROG: that path lives inside the git checkout $CHECKOUT — a .pkg payload would overwrite tracked source files with the bytes of whatever tag it was built from (CONTRACT §74; live incident 2026-09-07, issue #333)." >&2
    echo "$PROG: update that checkout yourself instead:  cd \"$CHECKOUT\" && git pull && bash install.sh" >&2
    exit 3
fi

exit 0
