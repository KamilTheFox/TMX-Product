import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'login_screen.dart';
import 'report_screen.dart';

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  Future<void> _logout(BuildContext context) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('token');
    if (context.mounted) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()),
      );
    }
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

            // Карточка — создать репорт
            _ActionCard(
              icon: Icons.camera_alt_outlined,
              title: 'Новый репорт',
              subtitle: 'Сфотографировать дефект и отправить',
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const ReportScreen()),
              ),
            ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }
}

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
                  Text(
                    title,
                    style: const TextStyle(
                      color: Color(0xFF1A1F2B),
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    subtitle,
                    style: const TextStyle(
                      color: Color(0xFF667085),
                      fontSize: 13,
                    ),
                  ),
                ],
              ),
            ),
            const Icon(
              Icons.arrow_forward_ios,
              color: Color(0xFF004F9E),
              size: 16,
            ),
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
          const Icon(
            Icons.info_outline,
            color: Color(0xFF5CC4F2),
            size: 24,
          ),
          const SizedBox(width: 16),
          const Expanded(
            child: Text(
              'При создании репорта геолокация определяется автоматически для выбора ближайшей линии',
              style: TextStyle(
                color: Color(0xFF667085),
                fontSize: 13,
                height: 1.5,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
