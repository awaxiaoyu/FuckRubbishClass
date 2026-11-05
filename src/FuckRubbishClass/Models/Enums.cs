namespace FuckRubbishClass.Models;

public enum PlatformType
{
    Zhihuishu,
    Chaoxing,
    Ketangpai,
    Custom
}

public enum AccountStatus
{
    Active,
    NeedLogin,
    LoggingIn,
    Offline,
    Error
}

public enum ModelRole
{
    FastSolver,      // 快速答题 (单选/多选/判断等高频客观题，低延迟高性价比)
    Vision,          // 视觉识图 (公式截图/图表题/手写字/课件截图识别)
    DeepReasoning,   // 深度推理 (主观大题/复杂辨析/逻辑推理)
    DocumentParser   // 课件文档解析 (Moonshot Files/课件PPT文本结构化)
}

public enum ModelProviderType
{
    DeepSeek,
    OpenAI,
    Moonshot,
    SiliconFlow,
    Claude,
    Qwen,
    Ollama,
    Custom
}

public enum TaskExecutionStatus
{
    Idle,
    Queued,
    Running,
    Paused,
    Completed,
    Failed,
    Cancelled
}

public enum LogLevel
{
    Info,
    Success,
    Warning,
    Error,
    Debug
}

public enum FeatureType
{
    VideoStudy,
    HomeworkQuiz,
    ExamAssessment,
    AttendanceSignIn,
    AiCourse
}
