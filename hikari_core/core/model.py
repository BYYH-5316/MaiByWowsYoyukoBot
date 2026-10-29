import threading
import time
from typing import List, Optional, Protocol, Union, runtime_checkable, Any

from pydantic import BaseModel, Field

# repr 递归保护用的线程局部标志（见 Hikari_Model.__repr__）
_REPR_STATE = threading.local()


@runtime_checkable
class Func(Protocol):
    async def __call__(self, hikari: 'Hikari_Model'):
        ...


class UserInfo_Model(BaseModel):
    Platform: str = 'QQ'
    PlatformId: str = '2622749113'
    BotId: str = 'None'
    GroupId: Optional[str] = None


class ShipInfo(dict):
    """支持属性访问的字典"""

    def __getattr__(self, name):
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value

    def __deepcopy__(self, memo):
        """支持深度复制"""
        return ShipInfo(self.copy())


class Input_Model(BaseModel):
    Command_Text: str = Field(default='', description='输入的指令,请提前去除wws')  # 输入的指令,请提前去除wws
    Command_List: List[str] = Field(default_factory=list)
    Search_Type: Optional[int] = 3  # 1:me  3:server+name or default
    Platform: Optional[str] = None
    PlatformId: Optional[str] = None
    Server: Optional[str] = None
    AccountName: Optional[str] = None
    AccountId: Optional[int] = None
    ClanName: Optional[str] = None
    ClanId: Optional[int] = None
    CwSeasonId: Optional[int] = 0
    Recent_Day: Optional[int] = 0
    Recent_Date: Optional[str] = time.strftime('%Y-%m-%d', time.localtime())
    ShipsMin: Optional[int] = 0  # ships 筛选最小等级，0 不限制
    ShipsMax: Optional[int] = 0  # ships 筛选最大等级，0 不限制
    Select_Index: Optional[int] = None
    Select_Data: Optional[List] = None
    ShipInfo: Any = Field(default_factory=ShipInfo)


class Output_Model(BaseModel):
    Yuyuko_Code: Optional[int] = None
    Data_Type: str = Field(default='', description='返回的数据类型')
    Data: Union[str, int, bytes, List[Any]] = Field(default=None, description='返回的数据')
    Template: Optional[str] = None
    Width: Optional[int] = None
    Height: Optional[int] = None


class Hikari_Model(BaseModel):
    Status: str = 'init'  # init:初始化 success:请求成功  failed:请求成功但API有错误或空返回  error:异常及本地错误
    StatusText: str = 'init'  # 默认走图片渲染模式
    UserInfo: UserInfo_Model = UserInfo_Model()
    Function: Func = Field(default=None, description='当前请求的函数')
    template_content: str = Field(default='', description='模板内容')
    Input: Input_Model = Field(default=Input_Model(), description='输入数据')
    Output: Output_Model = Field(default=Output_Model(), description='输出数据')

    class Config:
        arbitrary_types_allowed = True

    def __repr__(self) -> str:
        """递归安全的 repr。

        pydantic 默认的 __repr__ 会把字段一层层展开，而本模型是允许「自己装自己」的：
        例如 get_AccountIdByName 查不到账号时返回的是 hikari 本身（failed() 返回 self），
        调用方写成 hikari.Input.AccountId = <该返回值> 之后，模型就通过 Input.AccountId
        持有自身引用 —— 此时任何 str()/repr()/f-string 都会顺着
        Hikari_Model -> Input -> AccountId -> Hikari_Model ... 无限展开，
        最终抛 RecursionError（线上 09-12 01:30 那次就是这么炸的）。
        这里在回环处截断；没有环时仍然是完整的 pydantic repr。
        """
        if getattr(_REPR_STATE, 'busy', False):
            return f'{type(self).__name__}(...)'
        _REPR_STATE.busy = True
        try:
            return f'{self.__repr_name__()}({self.__repr_str__(", ")})'
        finally:
            _REPR_STATE.busy = False

    def __str__(self) -> str:
        # str() 走的是同一套字段展开，同样要防环
        if getattr(_REPR_STATE, 'busy', False):
            return f'{type(self).__name__}(...)'
        _REPR_STATE.busy = True
        try:
            return self.__repr_str__(' ')
        finally:
            _REPR_STATE.busy = False

    @property
    def is_me(self) -> bool:
        """当前查询是否为 me 模式（Search_Type == 1）"""
        return self.Input.Search_Type == 1

    def error(self, error_data):
        self.Status = 'error'
        self.Output.Data = error_data
        self.Output.Data_Type = str(type(error_data))
        return self

    def success(self, success_data):
        self.Status = 'success'
        self.Output.Data = success_data
        self.Output.Data_Type = str(type(success_data))
        return self

    def failed(self, failed_data):
        self.Status = 'failed'
        self.Output.Data = failed_data
        self.Output.Data_Type = str(type(failed_data))
        return self

    def wait(self, select_data: List):
        self.Status = 'wait'
        self.Input.Select_Data = select_data
        self.Output.Data = select_data
        self.Output.Data_Type = str(type(self.Output.Data))
        return self
