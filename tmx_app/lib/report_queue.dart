import 'dart:io';
import 'dart:convert';
import 'dart:developer' as developer;
import 'package:shared_preferences/shared_preferences.dart';
import 'api/api_client.dart';

/// Один элемент очереди
class QueuedReport {
  final String id;
  final String photoPath;
  final String wagon;
  final String category;
  final String? textProb;
  final DateTime createdAt;

  QueuedReport({
    required this.id,
    required this.photoPath,
    required this.wagon,
    required this.category,
    this.textProb,
    required this.createdAt,
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'photoPath': photoPath,
        'wagon': wagon,
        'category': category,
        'textProb': textProb,
        'createdAt': createdAt.toIso8601String(),
      };

  factory QueuedReport.fromJson(Map<String, dynamic> json) => QueuedReport(
        id: json['id'],
        photoPath: json['photoPath'],
        wagon: json['wagon'],
        category: json['category'],
        textProb: json['textProb'],
        createdAt: DateTime.parse(json['createdAt']),
      );
}

/// Очередь отложенных репортов — хранится в SharedPreferences
class ReportQueue {
  static const _key = 'pending_reports';

  /// Добавить репорт в очередь
  static Future<void> enqueue(QueuedReport report) async {
    final prefs = await SharedPreferences.getInstance();
    final list = _load(prefs);
    list.add(report);
    await _save(prefs, list);
    developer.log(
      'ReportQueue: enqueued id=${report.id}  total=${list.length}',
      name: 'ReportQueue',
    );
  }

  /// Получить все ожидающие репорты
  static Future<List<QueuedReport>> getAll() async {
    final prefs = await SharedPreferences.getInstance();
    return _load(prefs);
  }

  /// Удалить репорт из очереди после успешной отправки
  static Future<void> remove(String id) async {
    final prefs = await SharedPreferences.getInstance();
    final list = _load(prefs);
    list.removeWhere((r) => r.id == id);
    await _save(prefs, list);
    developer.log('ReportQueue: removed id=$id  remaining=${list.length}',
        name: 'ReportQueue');
  }

  /// Количество ожидающих репортов
  static Future<int> count() async {
    final prefs = await SharedPreferences.getInstance();
    return _load(prefs).length;
  }

  static List<QueuedReport> _load(SharedPreferences prefs) {
    final raw = prefs.getStringList(_key) ?? [];
    return raw
        .map((s) {
          try {
            return QueuedReport.fromJson(jsonDecode(s));
          } catch (e) {
            developer.log('ReportQueue: failed to parse item: $e',
                name: 'ReportQueue');
            return null;
          }
        })
        .whereType<QueuedReport>()
        .toList();
  }

  static Future<void> _save(
      SharedPreferences prefs, List<QueuedReport> list) async {
    await prefs.setStringList(
      _key,
      list.map((r) => jsonEncode(r.toJson())).toList(),
    );
  }

  /// Попытаться отправить все репорты из очереди.
  /// Возвращает количество успешно отправленных.
  static Future<int> flush() async {
    final queue = await getAll();
    if (queue.isEmpty) return 0;

    developer.log('ReportQueue: flushing ${queue.length} item(s)',
        name: 'ReportQueue');

    int sent = 0;
    for (final report in queue) {
      final file = File(report.photoPath);
      if (!file.existsSync()) {
        // Файл удалён — убираем из очереди, отправить уже нечего
        developer.log(
          'ReportQueue: photo not found, dropping id=${report.id}  path=${report.photoPath}',
          name: 'ReportQueue',
        );
        await remove(report.id);
        continue;
      }

      final ok = await ApiClient.sendReport(
        photo: file,
        wagon: report.wagon,
        category: report.category,
        textProb: report.textProb,
      );

      if (ok) {
        await remove(report.id);
        sent++;
        developer.log('ReportQueue: sent id=${report.id}', name: 'ReportQueue');
      } else {
        developer.log('ReportQueue: failed id=${report.id}, will retry later',
            name: 'ReportQueue');
      }
    }

    return sent;
  }
}
