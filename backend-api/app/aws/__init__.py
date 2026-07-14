"""AWS adapter 套件（Speaker Attribution）。

SOLID：``ports`` 定義窄介面（ISP/DIP），每個服務 ``Real*``/``Stub*`` 各自實作（LSP），
``factory`` 依設定綁定。boto3 只在 Real* 具體實作內 lazy import。
"""
