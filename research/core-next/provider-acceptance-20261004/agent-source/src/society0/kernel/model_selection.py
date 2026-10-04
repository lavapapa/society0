"""显式选择已有提供方；不创建额外连接池或修改会话身份。"""


class ModelResolver:
    def __init__(self,profiles,default,*,actor_models=None,type_models=None):
        self.profiles=profiles
        self.default=default
        self.actor_models=dict(actor_models or {})
        self.type_models=dict(type_models or {})
        for name in (default,*self.actor_models.values(),*self.type_models.values()):
            if name not in profiles:
                raise ValueError(f'unknown model profile: {name}')

    def resolve(self,actor_id,*,actor_type=None,override=None):
        name=(override if override is not None else
              self.actor_models.get(actor_id,self.type_models.get(actor_type,self.default)))
        return self.profiles[name]
