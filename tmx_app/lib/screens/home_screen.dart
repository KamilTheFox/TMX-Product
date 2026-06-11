import 'dart:developer' as developer;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'login_screen.dart';
import 'report_screen.dart';
import '../report_queue.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _pendingCount = 0;
  late final Stream<ConnectivityResult> _connectivityStream;

  @override
  void initState() {
    super.initState();
    _refreshPendingCount();

    // Слушаем появление сети — при восстановлении автоматически флашим очередь
    _connectivityStream = Connectivity().onConnectivityChanged;
    _connectivityStream.listen((result) async {
      final hasNetwork = result != ConnectivityResult.none;
      if (hasNetwork) {
        final sent = await ReportQueue.flush();
        if (sent > 0) {
          developer.log('Auto-flushed $sent report(s)', name: 'HomeScreen');
          await _refreshPendingCount();
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(
                content: Text(
                    'Отправлено $sent отложенн${_pluralReport(sent)} репорт${_pluralEnding(sent)}'),
                backgroundColor: const Color(0xFF2EAF64),
              ),
            );
          }
        }
      }
    });
  }

  Future<void> _refreshPendingCount() async {
    final count = await ReportQueue.count();
    if (mounted) setState(() => _pendingCount = count);
  }

  Future<void> _logout(BuildContext context) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('token');
    if (context.mounted) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()),
      );
    }
  }

  String _pluralReport(int n) {
    if (n % 10 == 1 && n % 100 != 11) return 'ый';
    if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20))
      return 'ых';
    return 'ых';
  }

  String _pluralEnding(int n) {
    if (n % 10 == 1 && n % 100 != 11) return '';
    if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20))
      return 'а';
    return 'ов';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'TMX',
          style: TextStyle(
            letterSpacing: 4,
            fontWeight: FontWeight.bold,
            color: Color(0xFF004F9E),
          ),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.logout, color: Color(0xFF667085)),
            tooltip: 'Выйти',
            onPressed: () => _logout(context),
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SizedBox(height: 16),
            const Text(
              'Добро пожаловать',
              style: TextStyle(
                color: Color(0xFF667085),
                fontSize: 14,
                letterSpacing: 1,
              ),
            ),
            const SizedBox(height: 8),
            const Text(
              'Система контроля\nдефектов подвижного\nсостава',
              style: TextStyle(
                color: Color(0xFF1A1F2B),
                fontSize: 28,
                fontWeight: FontWeight.bold,
                height: 1.3,
              ),
            ),
            const SizedBox(height: 48),

            // Баннер очереди
            if (_pendingCount > 0) ...[
              _PendingBanner(
                count: _pendingCount,
                onRetry: () async {
                  final sent = await ReportQueue.flush();
                  await _refreshPendingCount();
                  if (mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        content: Text(sent > 0
                            ? 'Отправлено $sent репорт(ов)'
                            : 'Нет связи, попробуйте позже'),
                        backgroundColor: sent > 0
                            ? const Color(0xFF2EAF64)
                            : const Color(0xFFD64545),
                      ),
                    );
                  }
                },
              ),
              const SizedBox(height: 16),
            ],

            // Карточка — создать репорт
            _ActionCard(
              icon: Icons.camera_alt_outlined,
              title: 'Новый репорт',
              subtitle: 'Сфотографировать дефект и отправить',
              onTap: () async {
                await Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => const ReportScreen()),
                );
                // Обновляем счётчик после возврата с экрана репорта
                await _refreshPendingCount();
              },
            ),
            const SizedBox(height: 16),

            const _InfoCard(),
          ],
        ),
      ),
    );
  }
}

// ─── Баннер отложенных репортов ───────────────────────────────────────────────

class _PendingBanner extends StatelessWidget {
  final int count;
  final VoidCallback onRetry;

  const _PendingBanner({required this.count, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF8EC),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: const Color(0xFFF5A623)),
      ),
      child: Row(
        children: [
          const Icon(Icons.cloud_upload_outlined,
              color: Color(0xFFF5A623), size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              'Ожидает отправки: $count репорт${_plural(count)}',
              style: const TextStyle(color: Color(0xFF1A1F2B), fontSize: 13),
            ),
          ),
          TextButton(
            onPressed: onRetry,
            style: TextButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: const Text(
              'Отправить',
              style: TextStyle(
                color: Color(0xFF004F9E),
                fontSize: 13,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _plural(int n) {
    if (n % 10 == 1 && n % 100 != 11) return '';
    if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20))
      return 'а';
    return 'ов';
  }
}

// ─── Остальные виджеты ────────────────────────────────────────────────────────

class _ActionCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;

  const _ActionCard({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          color: const Color(0xFFF8FAFC),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: const Color(0xFF004F9E), width: 1),
          boxShadow: const [
            BoxShadow(
              color: Color(0x1F004F9E),
              blurRadius: 8,
              offset: Offset(0, 2),
            ),
          ],
        ),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFFE8F7FE),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Icon(icon, color: const Color(0xFF004F9E), size: 28),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: const TextStyle(
                          color: Color(0xFF1A1F2B),
                          fontSize: 16,
                          fontWeight: FontWeight.bold)),
                  const SizedBox(height: 4),
                  Text(subtitle,
                      style: const TextStyle(
                          color: Color(0xFF667085), fontSize: 13)),
                ],
              ),
            ),
            const Icon(Icons.arrow_forward_ios,
                color: Color(0xFF004F9E), size: 16),
          ],
        ),
      ),
    );
  }
}

class _InfoCard extends StatelessWidget {
  const _InfoCard();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: const Color(0xFFF2F6FA),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: const Color(0xFFD9E2EC), width: 1),
      ),
      child: const Row(
        children: [
          Icon(Icons.info_outline, color: Color(0xFF5CC4F2), size: 24),
          SizedBox(width: 16),
          Expanded(
            child: Text(
              'Репорты сохраняются локально и отправляются автоматически при появлении сети',
              style: TextStyle(
                  color: Color(0xFF667085), fontSize: 13, height: 1.5),
            ),
          ),
        ],
      ),
    );
  }
}
