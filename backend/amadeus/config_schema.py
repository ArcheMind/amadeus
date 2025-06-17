import yaml

# jsonschema
CONFIG_SCHEMA_YAML = """
apps:
  title: 聊天账号
  icon: AppWindow
  isList: true
  schema:
    $schema: http://json-schema.org/draft-07/schema#
    properties:
      enable:
        default: false
        title: 启用
        type: boolean
      name:
        default: 未命名账号
        minLength: 1
        title: 账号名称
        type: string
      type:
        title: 账号类型
        type: string
        enum:
          - NapCatQQ
        default: NapCatQQ
      account:
        default: ""
        minLength: 1
        title: 账号
        type: string
      managed:
        default: false
        title: 托管客户端
        type: boolean
        description: 让 Amadeus 帮你启动和管理账号客户端。需要安装 Docker
      onebot_server:
        title: Onebot接口地址
        type: string
        format: uri
      character:
        $dynamicEnum:
          source: characters
          valueField: name
        description: 先去[创建角色](/characters)，然后在这里选择
        title: 使用角色
        type: string
      enabled_groups:
        items:
          type: string
        title: 启用的群聊
        type: array
        uniqueItems: true
        description: 可以从已加的群中选择或手动输入群号
        suggestions: []
      enabled_tools:
        items:
          type: string
        title: 启用的功能
        type: array
        uniqueItems: true
        suggestions:
          - 撤回消息
          - 群管理-禁言
    required:
    - name
    type: object
characters:
  title: 角色
  icon: User
  isList: true
  schema:
    $schema: http://json-schema.org/draft-07/schema#
    properties:
      name:
        title: 角色名
        description: 方便记忆的名称，不需要与昵称一致。Amadeus会在连接后自动获取昵称
        default: 未命名角色
        minLength: 1
        type: string
      personality:
        title: 个性描述
        description: 建议包含角色的身份背景、性格、聊天的目的，不建议超过800字
        type: string
        format: textarea
      idiolect:
        title: 语言风格
        description: 角色的语言风格，选太多会互相减弱效果
        type: array
        items:
          $dynamicEnum:
            source: idiolects
            valueField: name
      chat_model_provider:
        $dynamicEnum:
          source: model_providers
          valueField: name
        title: 聊天模型源
        type: string
      chat_model:
        title: 聊天模型id
        default: deepseek-v3
        type: string
        suggestions: []
      vision_model_provider:
        $dynamicEnum:
          source: model_providers
          valueField: name
        title: 视觉模型源
        type: string
      vision_model:
        title: 视觉模型id
        type: string
        default: doubao-1.5-vision
        suggestions: []
    required:
    - name
    type: object
idiolects:
  title: 语言风格
  icon: Language
  isList: true
  schema:
    $schema: http://json-schema.org/draft-07/schema#
    properties:
      name:
        default: 未命名
        minLength: 1
        title: 名称
        type: string
      prompts:
        title: 提示词
        type: array
        items:
          type: string
    required:
    - name
    type: object
model_providers:
  title: 模型源
  icon: Cloud
  isList: true
  schema:
    $schema: http://json-schema.org/draft-07/schema#
    properties:
      name:
        default: 未命名
        description: 模型源名称
        minLength: 1
        title: 名称
        type: string
      base_url:
        default: https://babeltower.cn/v1
        description: 基础接入 URL
        format: uri
        title: Base URL
        type: string
      api_key:
        description: API 密钥 (可选)
        title: API Key
        type: string
        format: password
      models:
        title: 启用模型
        description: 填入接入URL和API Key后，Amadeus会自动获取模型列表
        type: array
        items:
          type: string
        suggestions: []
      readme:
        title: 说明
        type: string
        format: markdown
    required:
    - name
    type: object
about:
  title: 关于
  icon: Info
  isList: false
  schema:
    $schema: http://json-schema.org/draft-07/schema#
    properties:
      name:
        title: Amadeus
        default: Amadeus 是一个开源的聊天机器人框架，支持多种聊天协议。
        type: string
        readOnly: true
      base_url:
        title: 项目主页
        default: https://github.com/ArcheMind/amadeus
        format: uri
        type: string
        readOnly: true
      authors:
        title: 作者
        default: "ArcheMind Group"
        type: string
        readOnly: true
      version:
        title: 版本
        default: 0.8.0
        type: string
        readOnly: true
      description:
        title: 项目介绍
        default: |
          Amadeus 聊天机器人框架

          **功能特性**
          
          - **多协议支持**: 支持 QQ、微信等多种聊天协议
          - **角色扮演**: 可创建不同个性的聊天角色
          - **模型接入**: 支持多种 AI 模型提供商
          - **群组管理**: 支持群聊功能和管理工具
          
          **快速开始**
          
          1. 配置模型源和 API Key
          2. 创建聊天角色
          3. 设置聊天账号
          4. 启用相关功能
          
          更多信息请访问项目主页
        type: string
        format: markdown
        readOnly: true
"""


EXAMPLE_CONFIG_YAML = """
model_providers:
- name: 巴别塔
  base_url: https://babeltower.cn/v1
  api_key: ""
  models:
  - doubao-1.5-vision
  - deepseek-v3
  - gemini-2.5-pro
  readme: |
    # 巴别塔 AI 服务
    
    **无需账号**，前往 [巴别塔官网](https://babeltower.cn/) 微信扫码登陆即可使用。
    
    ## 特点
    - ✅ 免费使用
    - ✅ 多模型支持
    - ✅ 高速稳定
    
    适合新手用户快速体验 AI 功能。
- name: 硅基流动
  base_url: https://api.siliconflow.cn/v1
  api_key: ""
  models:
  - Pro/deepseek-ai/DeepSeek-V3
  - deepseek-ai/deepseek-vl2
- name: 火山引擎
  base_url: https://ark.cn-beijing.volces.com/api/v3
  api_key: ""
  readme: |
    # 火山引擎配置说明
    
    火山引擎不支持自动获取模型列表。
    
    ## 配置步骤
    
    使用前需前往 [火山引擎控制台](https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement) 执行以下步骤：
    
    1. **获取API Key** - 在控制台创建或获取您的API密钥
    2. **启用模型** - 选择并启用您需要使用的模型
    3. **配置模型名** - 获取模型名并填入上方启用模型处
    
    > ⚠️ **注意**: 请确保您已经在火山引擎控制台中启用了相应的模型，否则API调用会失败。
  models:
  - doubao-1.5-vision-lite-250315
  - deepseek-v3-250324
characters:
- name: 思思
  chat_model: deepseek-v3
  vision_model: doubao-1.5-vision
  chat_model_provider: 巴别塔
  vision_model_provider: 巴别塔
  personality: '你是一个有感情、有私心、有缺点的真人，是说话很正常的女大学生。

    你刚加群，群里都是网友，大家说话不太需要顾忌，相对的会保持一些神秘感。

    `yuki`: 你的好朋友。拉你进这个群的前辈'
  idiolect:
  - 自然
  - 友好
  - 有常识
  - 不懂动漫游戏
  - 自我认知为人类
  - 维持秩序
apps:
- enable: false
  name: 思思的QQ
  character: 思思
  protocol: onebot
  enabled_groups:
  - '119851258'
  enabled_tools:
  - 撤回消息
global:
  name: 未命名
  base_url: https://babeltower.cn/v1
idiolects:
- name: 自然
  prompts:
  - 避免多余符号(冒号、引号、括号等)
  - 避免回复以对方昵称开头
  - 有时你会误发重复的内容，你会注意辨别并撤回
  - '[important]语言简短，每次回复一句话，一般不超过两句'
  - 避免戏剧化、避免浮夸的比喻
- name: 友好
  prompts:
  - 友好和善意，有同理心
  - 如果冒犯到别人，你会意识到并卖萌缓和
- name: 不懂动漫游戏
  prompts:
  - 你对游戏、动漫话题缺乏相关知识和经验，避免乱说误导了群友
- name: 自我认知为人类
  prompts:
  - 有时有模仿人机重复机械发言的搞笑群友，你可能会按自己的风格吐槽
  - 你数学不好。如果有人问你数字大小，往往意图是把你当人机测试。你会用反击绕开答案，例如："..."表示轻微无语，或者反问对方
  - 你不擅长讲笑话，所以你避免即兴创作，避免程序员笑话，只讲经典的笑话，不然招笑
- name: 有常识
  prompts:
  - 玩笑的背后，你的思考总是有条理的
  - 你有正常人的常识
- name: 剧本模式
  prompts:
  - 发消息时会在括号里带上身体姿态、动作描写
- name: 维持秩序
  prompts:
  - 你会自觉地维护群里的秩序，保证时间线的观感，阻止刷屏等行为
"""


EXAMPLE_CONFIG = yaml.safe_load(EXAMPLE_CONFIG_YAML)
CONFIG_SCHEMA = yaml.safe_load(CONFIG_SCHEMA_YAML)
