from google.api import field_behavior_pb2 as _field_behavior_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class MetadataExchangeRequest(_message.Message):
    __slots__ = ["user_agent", "auth_type", "oauth2_token"]
    class AuthType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = []
        AUTH_TYPE_UNSPECIFIED: _ClassVar[MetadataExchangeRequest.AuthType]
        DB_NATIVE: _ClassVar[MetadataExchangeRequest.AuthType]
        AUTO_IAM: _ClassVar[MetadataExchangeRequest.AuthType]
    AUTH_TYPE_UNSPECIFIED: MetadataExchangeRequest.AuthType
    DB_NATIVE: MetadataExchangeRequest.AuthType
    AUTO_IAM: MetadataExchangeRequest.AuthType
    USER_AGENT_FIELD_NUMBER: _ClassVar[int]
    AUTH_TYPE_FIELD_NUMBER: _ClassVar[int]
    OAUTH2_TOKEN_FIELD_NUMBER: _ClassVar[int]
    user_agent: str
    auth_type: MetadataExchangeRequest.AuthType
    oauth2_token: str
    def __init__(self, user_agent: _Optional[str] = ..., auth_type: _Optional[_Union[MetadataExchangeRequest.AuthType, str]] = ..., oauth2_token: _Optional[str] = ...) -> None: ...

class MetadataExchangeResponse(_message.Message):
    __slots__ = ["response_code", "error"]
    class ResponseCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = []
        RESPONSE_CODE_UNSPECIFIED: _ClassVar[MetadataExchangeResponse.ResponseCode]
        OK: _ClassVar[MetadataExchangeResponse.ResponseCode]
        ERROR: _ClassVar[MetadataExchangeResponse.ResponseCode]
    RESPONSE_CODE_UNSPECIFIED: MetadataExchangeResponse.ResponseCode
    OK: MetadataExchangeResponse.ResponseCode
    ERROR: MetadataExchangeResponse.ResponseCode
    RESPONSE_CODE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    response_code: MetadataExchangeResponse.ResponseCode
    error: str
    def __init__(self, response_code: _Optional[_Union[MetadataExchangeResponse.ResponseCode, str]] = ..., error: _Optional[str] = ...) -> None: ...
