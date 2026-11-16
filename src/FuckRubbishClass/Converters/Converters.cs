using FuckRubbishClass.Models;
using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;

namespace FuckRubbishClass.Converters;

public sealed class StringEqualsConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
        string.Equals(value as string, parameter as string, StringComparison.Ordinal);
    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
        value is true ? parameter : Binding.DoNothing;
}

public class BoolToVisibilityConverter : IValueConverter
{
    public bool Invert { get; set; }

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var b = value is true;
        if (Invert) b = !b;
        return b ? Visibility.Visible : Visibility.Collapsed;
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var v = value is Visibility.Visible;
        return Invert ? !v : v;
    }
}

public class StringNullOrEmptyToVisibilityConverter : IValueConverter
{
    public bool Invert { get; set; }

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var empty = string.IsNullOrWhiteSpace(value as string);
        if (Invert) empty = !empty;
        return empty ? Visibility.Collapsed : Visibility.Visible;
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotImplementedException();
}

public class PlatformToColorConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        if (value is PlatformType platform)
        {
            return platform switch
            {
                PlatformType.Zhihuishu => new SolidColorBrush(Color.FromRgb(22, 163, 74)),   // Green
                PlatformType.Chaoxing => new SolidColorBrush(Color.FromRgb(37, 99, 235)),    // Blue
                PlatformType.Ketangpai => new SolidColorBrush(Color.FromRgb(234, 88, 12)),  // Orange
                _ => new SolidColorBrush(Color.FromRgb(100, 116, 139))
            };
        }
        return new SolidColorBrush(Color.FromRgb(100, 116, 139));
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotImplementedException();
}

public class PlatformToSoftBgConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        if (value is PlatformType platform)
        {
            return platform switch
            {
                PlatformType.Zhihuishu => new SolidColorBrush(Color.FromRgb(240, 253, 244)),  // Soft Green
                PlatformType.Chaoxing => new SolidColorBrush(Color.FromRgb(239, 246, 255)),   // Soft Blue
                PlatformType.Ketangpai => new SolidColorBrush(Color.FromRgb(255, 247, 237)), // Soft Orange
                _ => new SolidColorBrush(Color.FromRgb(248, 250, 252))
            };
        }
        return new SolidColorBrush(Color.FromRgb(248, 250, 252));
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotImplementedException();
}

public class AccountStatusToColorConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        if (value is AccountStatus status)
        {
            return status switch
            {
                AccountStatus.Active => new SolidColorBrush(Color.FromRgb(16, 185, 129)),   // Green
                AccountStatus.NeedLogin => new SolidColorBrush(Color.FromRgb(245, 158, 11)), // Amber
                AccountStatus.LoggingIn => new SolidColorBrush(Color.FromRgb(59, 130, 246)), // Blue
                AccountStatus.Error => new SolidColorBrush(Color.FromRgb(239, 68, 68)),      // Red
                _ => new SolidColorBrush(Color.FromRgb(148, 163, 184))                      // Gray
            };
        }
        return new SolidColorBrush(Color.FromRgb(148, 163, 184));
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotImplementedException();
}

public class LatencyToColorConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        if (value is long latency)
        {
            if (latency < 0) return new SolidColorBrush(Color.FromRgb(148, 163, 184));
            if (latency < 300) return new SolidColorBrush(Color.FromRgb(16, 185, 129));
            if (latency < 800) return new SolidColorBrush(Color.FromRgb(245, 158, 11));
            return new SolidColorBrush(Color.FromRgb(239, 68, 68));
        }
        return new SolidColorBrush(Color.FromRgb(148, 163, 184));
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => throw new NotImplementedException();
}
