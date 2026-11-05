using CommunityToolkit.Mvvm.Messaging;
using FuckRubbishClass.Core.Logging;
using System.Windows;

namespace FuckRubbishClass.Core.Events;

public class EventBus : IEventBus
{
    private readonly IMessenger _messenger;

    public EventBus(IMessenger? messenger = null)
    {
        _messenger = messenger ?? WeakReferenceMessenger.Default;
    }

    public void Publish<TEvent>(TEvent @event) where TEvent : class
    {
        ArgumentNullException.ThrowIfNull(@event);

        AppLogger.Debug("事件总线", $"[发布事件] {typeof(TEvent).Name}");
        _messenger.Send(@event);
    }

    public void Subscribe<TEvent>(object recipient, Action<TEvent> handler, bool dispatchOnUi = true) where TEvent : class
    {
        ArgumentNullException.ThrowIfNull(recipient);
        ArgumentNullException.ThrowIfNull(handler);

        _messenger.Register<TEvent>(recipient, (_, msg) =>
        {
            if (dispatchOnUi && Application.Current?.Dispatcher != null && !Application.Current.Dispatcher.CheckAccess())
            {
                Application.Current.Dispatcher.InvokeAsync(() => handler(msg));
            }
            else
            {
                handler(msg);
            }
        });
    }

    public void Unsubscribe<TEvent>(object recipient) where TEvent : class
    {
        ArgumentNullException.ThrowIfNull(recipient);
        _messenger.Unregister<TEvent>(recipient);
    }

    public void UnsubscribeAll(object recipient)
    {
        ArgumentNullException.ThrowIfNull(recipient);
        _messenger.UnregisterAll(recipient);
    }
}
