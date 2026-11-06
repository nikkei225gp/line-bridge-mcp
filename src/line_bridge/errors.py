class BridgeError(Exception):
    """Only static, non-sensitive codes may cross an RPC or logging boundary."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
