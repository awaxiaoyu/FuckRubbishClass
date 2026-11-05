using FuckRubbishClass.Models;
using System.Collections.ObjectModel;
using System.IO;
using System.Text.Json;

namespace FuckRubbishClass.Services;

public interface ICourseService
{
    ObservableCollection<CourseModel> Courses { get; }
    Task LoadCoursesAsync();
    Task SaveCoursesAsync();
    Task RefreshCoursesForAccountAsync(string accountId);
}

public class CourseService : ICourseService
{
    private readonly string _filePath;
    public ObservableCollection<CourseModel> Courses { get; } = new();

    public CourseService(string? dataDir = null)
    {
        var dir = dataDir ?? Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "data");
        Directory.CreateDirectory(dir);
        _filePath = Path.Combine(dir, "courses.json");
    }

    public async Task LoadCoursesAsync()
    {
        try
        {
            if (File.Exists(_filePath))
            {
                var json = await File.ReadAllTextAsync(_filePath);
                var loaded = JsonSerializer.Deserialize<List<CourseModel>>(json);
                if (loaded != null && loaded.Count > 0)
                {
                    Courses.Clear();
                    foreach (var c in loaded) Courses.Add(c);
                    return;
                }
            }
        }
        catch
        {
            // Seed defaults
        }

        SeedDefaultCourses();
        await SaveCoursesAsync();
    }

    private void SeedDefaultCourses()
    {
        Courses.Clear();

        // 智慧树课程
        Courses.Add(new CourseModel
        {
            CourseId = "2145892",
            ClassId = "982143",
            Name = "马克思主义基本原理概论",
            Platform = PlatformType.Zhihuishu,
            AccountDisplayName = "小宇同学",
            Teacher = "王教授",
            School = "清华大学",
            ProgressPercent = 88.5,
            ModeLabel = "视频课程",
            PendingHomeworkCount = 1,
            CoverColor = "#2563EB"
        });

        Courses.Add(new CourseModel
        {
            CourseId = "2198031",
            ClassId = "104821",
            Name = "人工智能导论与伦理（知到AI课）",
            Platform = PlatformType.Zhihuishu,
            AccountDisplayName = "小宇同学",
            Teacher = "李院士团队",
            School = "北京大学",
            ProgressPercent = 45.0,
            ModeLabel = "AI 智能课",
            IsAiCourse = true,
            PendingHomeworkCount = 2,
            CoverColor = "#7C3AED"
        });

        Courses.Add(new CourseModel
        {
            CourseId = "2087612",
            ClassId = "872134",
            Name = "军事理论与国家安全教育",
            Platform = PlatformType.Zhihuishu,
            AccountDisplayName = "小宇同学",
            Teacher = "张教官",
            School = "国防大学",
            ProgressPercent = 100.0,
            ModeLabel = "已结课",
            PendingHomeworkCount = 0,
            CoverColor = "#059669"
        });

        Courses.Add(new CourseModel
        {
            CourseId = "2156740",
            ClassId = "934201",
            Name = "大学生心理健康与人际交往",
            Platform = PlatformType.Zhihuishu,
            AccountDisplayName = "小宇同学",
            Teacher = "陈导师",
            School = "华东师范",
            ProgressPercent = 62.0,
            ModeLabel = "共享学分课",
            PendingHomeworkCount = 0,
            CoverColor = "#0284C7"
        });

        // 学习通课程
        Courses.Add(new CourseModel
        {
            CourseId = "2389104",
            ClassId = "552190",
            Name = "毛泽东思想和中国特色社会主义理论体系概论",
            Platform = PlatformType.Chaoxing,
            AccountDisplayName = "xiaoyu_cx",
            Teacher = "刘副教授",
            School = "厦门大学",
            ProgressPercent = 74.0,
            ModeLabel = "视频 + 章节测验",
            PendingHomeworkCount = 3,
            PendingExamCount = 1,
            CoverColor = "#D97706"
        });

        Courses.Add(new CourseModel
        {
            CourseId = "2401823",
            ClassId = "619022",
            Name = "近代史纲要专题",
            Platform = PlatformType.Chaoxing,
            AccountDisplayName = "xiaoyu_cx",
            Teacher = "黄老师",
            School = "厦门大学",
            ProgressPercent = 95.0,
            ModeLabel = "期末考试周",
            PendingHomeworkCount = 0,
            PendingExamCount = 1,
            CoverColor = "#DC2626"
        });

        Courses.Add(new CourseModel
        {
            CourseId = "2310928",
            ClassId = "481029",
            Name = "创新创业基础与实务",
            Platform = PlatformType.Chaoxing,
            AccountDisplayName = "xiaoyu_cx",
            Teacher = "赵导师",
            School = "厦门大学",
            ProgressPercent = 30.0,
            ModeLabel = "视频任务",
            PendingHomeworkCount = 1,
            CoverColor = "#4F46E5"
        });

        // 课堂派课程
        Courses.Add(new CourseModel
        {
            CourseId = "ktp_cs_01",
            ClassId = "KTP889102",
            Name = "计算机网络原理 (周二/四)",
            Platform = PlatformType.Ketangpai,
            AccountDisplayName = "课堂派自动签到",
            Teacher = "郑教授",
            School = "计算机系",
            ProgressPercent = 90.0,
            ModeLabel = "考勤监测",
            PendingHomeworkCount = 0,
            CoverColor = "#EA580C"
        });

        Courses.Add(new CourseModel
        {
            CourseId = "ktp_cs_02",
            ClassId = "KTP992144",
            Name = "软件工程与敏捷开发",
            Platform = PlatformType.Ketangpai,
            AccountDisplayName = "课堂派自动签到",
            Teacher = "孙老师",
            School = "软件学院",
            ProgressPercent = 85.0,
            ModeLabel = "考勤监测",
            PendingHomeworkCount = 0,
            CoverColor = "#0891B2"
        });
    }

    public async Task SaveCoursesAsync()
    {
        try
        {
            var json = JsonSerializer.Serialize(Courses.ToList(), new JsonSerializerOptions { WriteIndented = true });
            await File.WriteAllTextAsync(_filePath, json);
        }
        catch
        {
            // Ignore write failures in read-only environments
        }
    }

    public async Task RefreshCoursesForAccountAsync(string accountId)
    {
        await Task.Delay(1200); // Simulate sync
        // Update timestamp or progress
        foreach (var c in Courses.Where(x => x.AccountId == accountId))
        {
            c.ProgressPercent = Math.Min(100.0, c.ProgressPercent + 5.0);
        }
        await SaveCoursesAsync();
    }
}
