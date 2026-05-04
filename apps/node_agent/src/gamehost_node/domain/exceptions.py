class NodeAgentError(Exception):
    code: str = "node_agent_error"
    status_code: int = 400
    title: str = "Node-agent error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title


class ContainerNotFound(NodeAgentError):
    code = "container_not_found"
    status_code = 404
    title = "Container not found"


class ContainerNameTaken(NodeAgentError):
    code = "container_name_taken"
    status_code = 409
    title = "Container name already taken"


class DockerUnavailable(NodeAgentError):
    code = "docker_unavailable"
    status_code = 503
    title = "Docker daemon unavailable"


class S3Unavailable(NodeAgentError):
    code = "s3_unavailable"
    status_code = 503
    title = "S3 backend unavailable"


class VolumeNotFound(NodeAgentError):
    code = "volume_not_found"
    status_code = 404
    title = "Volume not found"
