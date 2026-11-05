namespace FuckRubbishClass.Core.Events;

/// <summary>
/// 全局事件总线静态入口，支持在任何位置直接发布和弱引用订阅事件
/// </summary>
public static class AppEventBus
{
    private static IEventBus? _instance;

    public static void Initialize(IEventBus eventBus)
    {
        _instance = eventBus;
    }

    public static IEventBus Instance => _instance ??= new EventBus();

    public static void Publish<TEvent>(TEvent @event) where TEvent : class =>
        Instance.Publish(@event);

    public static void Subscribe<TEvent>(object recipient, Action<TEvent> handler, bool dispatchOnUi = true) where TEvent : class =>
        Instance.Subscribe(recipient, handler, dispatchOnUi);

    public static void Unsubscribe<TEvent>(object recipient) where TEvent : class =>
        Instance.Unsubscribe<TEvent>(recipient);

    public static void UnsubscribeAll(object recipient) =>
        Instance.UnsubscribeAll(recipient);
}
