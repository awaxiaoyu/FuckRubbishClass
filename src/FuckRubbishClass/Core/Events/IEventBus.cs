namespace FuckRubbishClass.Core.Events;

public interface IEventBus
{
    /// <summary>
    /// 发布强类型事件
    /// </summary>
    void Publish<TEvent>(TEvent @event) where TEvent : class;

    /// <summary>
    /// 弱引用订阅事件，默认自动调度到 UI 线程执行
    /// </summary>
    void Subscribe<TEvent>(object recipient, Action<TEvent> handler, bool dispatchOnUi = true) where TEvent : class;

    /// <summary>
    /// 取消某类事件的订阅
    /// </summary>
    void Unsubscribe<TEvent>(object recipient) where TEvent : class;

    /// <summary>
    /// 取消该对象接收的所有事件订阅
    /// </summary>
    void UnsubscribeAll(object recipient);
}
