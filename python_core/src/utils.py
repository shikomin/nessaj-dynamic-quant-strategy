"""共享工具模块"""

import os


def disable_system_proxy():
    """清除系统代理环境变量，使 AkShare 直连"""
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
                'ALL_PROXY', 'all_proxy'):
        os.environ.pop(key, None)
    os.environ['no_proxy'] = '*'
