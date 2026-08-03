class ServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class AgentExecutionError(ServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="agent_execution_failed",
            message="智能体暂时无法完成请求，请稍后重试。",
            status_code=502,
        )


class ServiceNotReadyError(ServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="service_not_ready",
            message="智能体服务尚未准备就绪。",
            status_code=503,
        )


class SessionNotFoundError(ServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="session_not_found",
            message="会话不存在或无权访问。",
            status_code=404,
        )
