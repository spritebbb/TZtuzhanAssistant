# -*- coding: utf-8 -*-
"""L12 公网网关包（离线骨架）。真机拓扑：独立 gateway 进程监听独立端口、只把该
端口交给 Cloudflare Tunnel，8801 保持 loopback；源站校验 Access JWT（access_auth）。
本包在批次15 只交付鉴权模块与说明，不启动真实监听。"""
