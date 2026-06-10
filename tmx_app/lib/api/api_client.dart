import 'dart:io';
import 'dart:developer' as developer;
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'package:flutter_dotenv/flutter_dotenv.dart';

/// Результат запроса send-code
enum SendCodeResult { success, banned, error }

class ApiClient {
  static String get baseUrl =>
      dotenv.env['API_BASE_URL'] ?? 'http://localhost:8000';

  static String? _token;

  static void setToken(String token) {
    _token = token;
    developer.log('Token set (length: ${token.length})', name: 'ApiClient');
  }

  static Map<String, String> get _authHeaders => {
        'Authorization': 'Bearer $_token',
        'Content-Type': 'application/json',
      };

  // ─── Auth ────────────────────────────────────────────────────────────────

  static Future<SendCodeResult> sendCode(String email) async {
    final url = '$baseUrl/auth/send-code';
    developer.log('POST $url  body: {"email":"$email"}', name: 'ApiClient');
    try {
      final res = await http.post(
        Uri.parse(url),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'email': email}),
      );
      developer.log('sendCode → ${res.statusCode}  body: ${res.body}',
          name: 'ApiClient');
      if (res.statusCode == 200) return SendCodeResult.success;
      try {
        final body = jsonDecode(res.body);
        if (body['detail'] == 'ban') {
          developer.log('sendCode: user is BANNED', name: 'ApiClient');
          return SendCodeResult.banned;
        }
      } catch (_) {}
      return SendCodeResult.error;
    } catch (e, st) {
      developer.log('sendCode error: $e',
          name: 'ApiClient', error: e, stackTrace: st);
      return SendCodeResult.error;
    }
  }

  static Future<String?> verify(String email, String code) async {
    final url = '$baseUrl/auth/verify';
    developer.log('POST $url  body: {"email":"$email","code":"$code"}',
        name: 'ApiClient');
    try {
      final res = await http.post(
        Uri.parse(url),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'email': email, 'code': code}),
      );
      developer.log('verify → ${res.statusCode}  body: ${res.body}',
          name: 'ApiClient');
      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);
        return data['access_token'];
      }
      return null;
    } catch (e, st) {
      developer.log('verify error: $e',
          name: 'ApiClient', error: e, stackTrace: st);
      return null;
    }
  }

  // ─── Categories ──────────────────────────────────────────────────────────

  static Future<List<dynamic>> getCategories() async {
    final url = '$baseUrl/categories';
    developer.log('GET $url', name: 'ApiClient');
    try {
      final res = await http.get(Uri.parse(url), headers: _authHeaders);
      developer.log('getCategories → ${res.statusCode}  body: ${res.body}',
          name: 'ApiClient');
      if (res.statusCode == 200) return jsonDecode(res.body);
      return [];
    } catch (e, st) {
      developer.log('getCategories error: $e',
          name: 'ApiClient', error: e, stackTrace: st);
      return [];
    }
  }

  // ─── РУДИМЕНТЫ (закомментированы) ────────────────────────────────────────
  // Линии, поезда, вагоны и геолокация больше не нужны на клиенте —
  // бэкенд сам подтягивает всё по номеру вагона.

  // static Future<List<dynamic>> getLines() async { ... }
  // static Future<List<dynamic>> getTrains(int lineId) async { ... }
  // static Future<List<dynamic>> getWagons(int trainId) async { ... }
  // static Future<Map<String, dynamic>?> getNearestLine(double lon, double lat) async { ... }

  // ─── Send report ─────────────────────────────────────────────────────────

  static Future<bool> sendReport({
    required File photo,
    required String wagon,
    required String category,
    String? textProb,
  }) async {
    final url = '$baseUrl/set_foto';
    developer.log(
      'POST $url (multipart)  wagon=$wagon  category=$category  '
      'photo=${photo.path}  text_prob=$textProb',
      name: 'ApiClient',
    );
    try {
      final request = http.MultipartRequest('POST', Uri.parse(url));
      request.headers['Authorization'] = 'Bearer $_token';
      request.files.add(await http.MultipartFile.fromPath('foto', photo.path));
      request.fields['wagon'] = wagon;
      request.fields['category'] = category;
      if (textProb != null && textProb.isNotEmpty) {
        request.fields['text_prob'] = textProb;
      }
      final streamed = await request.send();
      final res = await http.Response.fromStream(streamed);
      developer.log('sendReport → ${res.statusCode}  body: ${res.body}',
          name: 'ApiClient');
      return res.statusCode == 200;
    } catch (e, st) {
      developer.log('sendReport error: $e',
          name: 'ApiClient', error: e, stackTrace: st);
      return false;
    }
  }
}
