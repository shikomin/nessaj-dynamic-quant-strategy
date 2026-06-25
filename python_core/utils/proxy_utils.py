"""
网络代理工具
=============
处理系统代理环境变量，确保 HTTP 请求直连。
适用于 Windows 服务器上系统代理干扰 API 调用的场景。
"""
import os


def disable_system_proxy():
    """
    清除所有系统代理环境变量，使 zzshare / AkShare 直连网络。

    背景: Windows 服务器可能配置了系统级 HTTP 代理，
          导致 zzshare API 请求走代理失败 (Timeout / 502)。
          调用此函数后环境变量被清除，requests 库走直连。

    用法:
        from utils.proxy_utils import disable_system_proxy
        disable_system_proxy()
    """
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
                'ALL_PROXY', 'all_proxy'):
        os.environ.pop(key, None)
    os.environ['no_proxy'] = '*'
