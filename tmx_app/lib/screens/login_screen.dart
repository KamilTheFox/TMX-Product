import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../api/api_client.dart';
import 'home_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _emailController = TextEditingController();
  final _codeController = TextEditingController();

  bool _codeSent = false;
  bool _loading = false;
  String? _error;

  Future<void> _sendCode() async {
    final email = _emailController.text.trim();
    if (email.isEmpty) {
      setState(() => _error = 'Введите email');
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });

    final result = await ApiClient.sendCode(email);

    setState(() => _loading = false);

    switch (result) {
      case SendCodeResult.success:
        setState(() => _codeSent = true);
      case SendCodeResult.banned:
        setState(
            () => _error = 'Доступ заблокирован. Обратитесь к администратору.');
      case SendCodeResult.error:
        setState(() => _error = 'Ошибка отправки кода. Попробуйте позже.');
    }
  }

  Future<void> _verify() async {
    final email = _emailController.text.trim();
    final code = _codeController.text.trim();
    if (code.isEmpty) {
      setState(() => _error = 'Введите код');
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    final token = await ApiClient.verify(email, code);
    setState(() => _loading = false);
    if (token != null) {
      ApiClient.setToken(token);
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('token', token);
      if (mounted) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const HomeScreen()),
        );
      }
    } else {
      setState(() => _error = 'Неверный код');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFFFFFFF),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'TMX',
                style: TextStyle(
                  color: Color(0xFF004F9E),
                  fontSize: 36,
                  fontWeight: FontWeight.bold,
                  letterSpacing: 4,
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                'Система контроля дефектов',
                style: TextStyle(color: Color(0xFF667085), fontSize: 14),
              ),
              const SizedBox(height: 48),
              TextField(
                controller: _emailController,
                enabled: !_codeSent,
                keyboardType: TextInputType.emailAddress,
                style: const TextStyle(color: Color(0xFF1A1F2B)),
                decoration: const InputDecoration(labelText: 'Email'),
              ),
              const SizedBox(height: 16),
              if (_codeSent) ...[
                TextField(
                  controller: _codeController,
                  keyboardType: TextInputType.number,
                  style: const TextStyle(color: Color(0xFF1A1F2B)),
                  decoration: const InputDecoration(labelText: 'Код из письма'),
                ),
                const SizedBox(height: 16),
              ],
              if (_error != null) ...[
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  decoration: BoxDecoration(
                    color: const Color(0xFFFDF2F2),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(color: const Color(0xFFD64545)),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.block,
                          color: Color(0xFFD64545), size: 16),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          _error!,
                          style: const TextStyle(
                              color: Color(0xFFD64545), fontSize: 13),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 12),
              ],
              _loading
                  ? const Center(
                      child:
                          CircularProgressIndicator(color: Color(0xFF004F9E)),
                    )
                  : ElevatedButton(
                      onPressed: _codeSent ? _verify : _sendCode,
                      child: Text(_codeSent ? 'Войти' : 'Получить код'),
                    ),
              if (_codeSent) ...[
                const SizedBox(height: 12),
                TextButton(
                  onPressed: () => setState(() {
                    _codeSent = false;
                    _codeController.clear();
                    _error = null;
                  }),
                  child: const Text(
                    'Изменить email',
                    style: TextStyle(color: Color(0xFF667085)),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
