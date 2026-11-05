using FuckRubbishClass.ViewModels;
using System.Windows;
using System.Windows.Controls;

namespace FuckRubbishClass.Views;

public partial class FeaturesView : UserControl
{
    public FeaturesView()
    {
        InitializeComponent();
    }

    private void OnTabClicked(object sender, RoutedEventArgs e)
    {
        if (sender is RadioButton rb && rb.Tag is string tab && DataContext is FeaturesViewModel vm)
        {
            vm.ActiveFeatureTab = tab;
        }
    }
}
