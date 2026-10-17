"""Kernel seccomp offline subprocess wrapper, Linux-only; fail closed on error.

Blocks AF_UNIX as well as internet sockets to prevent uncontrolled local access.
Execs the child only after seccomp load. No network access is performed.
"""
from __future__ import annotations
import ctypes
import ctypes.util
import errno
import os
import sys

SYSCALLS = ('socket', 'socketpair', 'connect', 'bind', 'listen', 'accept', 'accept4',
            'sendto', 'sendmsg', 'sendmmsg', 'recvfrom', 'recvmsg', 'recvmmsg',
            'socketcall', 'io_uring_setup')

def install() -> None:
    if sys.platform != 'linux':
        raise RuntimeError('LINUX_SECCOMP_REQUIRED')
    name = ctypes.util.find_library('seccomp')
    if not name:
        raise RuntimeError('LIBSECCOMP_UNAVAILABLE')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), 'PR_SET_NO_NEW_PRIVS_FAILED')
    sc = ctypes.CDLL(name, use_errno=True)
    sc.seccomp_init.argtypes = [ctypes.c_uint32]
    sc.seccomp_init.restype = ctypes.c_void_p
    sc.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sc.seccomp_syscall_resolve_name.restype = ctypes.c_int
    sc.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    sc.seccomp_load.argtypes = [ctypes.c_void_p]
    sc.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = sc.seccomp_init(0x7fff0000)  # allow by default
    if not ctx:
        raise RuntimeError('SECCOMP_INIT_FAILED')
    try:
        for syscall in SYSCALLS:
            nr = sc.seccomp_syscall_resolve_name(syscall.encode('ascii'))
            if nr >= 0:
                result = sc.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, nr, 0)
                if result:
                    raise RuntimeError(f'SECCOMP_RULE_FAILED:{syscall}:{result}')
        if sc.seccomp_load(ctx):
            raise RuntimeError('SECCOMP_LOAD_FAILED')
    finally:
        sc.seccomp_release(ctx)

if __name__ == '__main__':
    if len(sys.argv) < 3:
        raise SystemExit('OFFLINE_EXEC_REQUIRES_COMMAND')
    install()
    print('OFFLINE_SECCOMP_LOADED', file=sys.stderr, flush=True)
    os.execv(sys.argv[1], sys.argv[1:])
