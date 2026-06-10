import 'dart:io';
import 'dart:developer' as developer;
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../api/api_client.dart';

class ReportScreen extends StatefulWidget {
  const ReportScreen({super.key});

  @override
  State<ReportScreen> createState() => _ReportScreenState();
}

class _ReportScreenState extends State<ReportScreen> {
  final _wagonController = TextEditingController();
  final _textController = TextEditingController();

  File? _photo;
  bool _loading = false;
  bool _sending = false;
  String? _error;

  List<dynamic> _categories = [];
  Map<String, dynamic>? _selectedCategory;

  @override
  void initState() {
    super.initState();
    _init();
  }

  @override
  void dispose() {
    _wagonController.dispose();
    _textController.dispose();
    super.dispose();
  }

  Future<void> _init() async {
    setState(() => _loading = true);
    await _loadCategories();
    setState(() => _loading = false);
  }

  Future<void> _loadCategories() async {
    final cats = await ApiClient.getCategories();
    setState(() => _categories = cats);
  }

  Future<void> _takePhoto() async {
    final picker = ImagePicker();
    final picked =
        await picker.pickImage(source: ImageSource.camera, imageQuality: 80);
    if (picked != null) setState(() => _photo = File(picked.path));
  }

  Future<void> _pickFromGallery() async {
    final picker = ImagePicker();
    final picked =
        await picker.pickImage(source: ImageSource.gallery, imageQuality: 80);
    if (picked != null) setState(() => _photo = File(picked.path));
  }

  Future<void> _submit() async {
    final wagon = _wagonController.text.trim();

    if (_photo == null) {
      setState(() => _error = 'Прикрепите фото');
      return;
    }
    if (wagon.isEmpty) {
      setState(() => _error = 'Введите номер вагона');
      return;
    }
    if (_selectedCategory == null) {
      setState(() => _error = 'Выберите тип дефекта');
      return;
    }

    setState(() {
      _sending = true;
      _error = null;
    });

    final ok = await ApiClient.sendReport(
      photo: _photo!,
      wagon: wagon,
      category: _selectedCategory!['id_categ'].toString(),
      textProb: _textController.text.trim(),
    );

    setState(() => _sending = false);

    if (ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Репорт успешно отправлен'),
          backgroundColor: Color(0xFF2EAF64),
        ),
      );
      Navigator.of(context).pop();
    } else {
      setState(() => _error = 'Ошибка отправки. Попробуйте ещё раз.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Новый репорт'),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.of(context).pop(),
        ),
      ),
      body: _loading
          ? const Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  CircularProgressIndicator(color: Color(0xFF004F9E)),
                  SizedBox(height: 16),
                  Text('Загрузка...',
                      style: TextStyle(color: Color(0xFF667085))),
                ],
              ),
            )
          : SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Фото
                  const _SectionLabel(label: 'ФОТО ДЕФЕКТА'),
                  const SizedBox(height: 8),
                  _PhotoBlock(
                    photo: _photo,
                    onCamera: _takePhoto,
                    onGallery: _pickFromGallery,
                  ),
                  const SizedBox(height: 24),

                  // Номер вагона
                  const _SectionLabel(label: 'НОМЕР ВАГОНА'),
                  const SizedBox(height: 8),
                  TextField(
                    controller: _wagonController,
                    keyboardType: TextInputType.text,
                    textCapitalization: TextCapitalization.characters,
                    style: const TextStyle(color: Color(0xFF1A1F2B)),
                    decoration: const InputDecoration(
                      hintText: 'Например: 1234 или АБВ-56',
                    ),
                  ),
                  const SizedBox(height: 16),

                  // Тип дефекта
                  const _SectionLabel(label: 'ТИП ДЕФЕКТА'),
                  const SizedBox(height: 8),
                  _Dropdown(
                    hint: 'Выберите тип дефекта',
                    value: _selectedCategory,
                    items: _categories.cast<Map<String, dynamic>>(),
                    labelKey: 'name',
                    onChanged: _categories.isEmpty
                        ? null
                        : (val) => setState(() => _selectedCategory = val),
                  ),
                  const SizedBox(height: 24),

                  // Описание
                  const _SectionLabel(
                      label: 'ОПИСАНИЕ ПРОБЛЕМЫ (необязательно)'),
                  const SizedBox(height: 8),
                  TextField(
                    controller: _textController,
                    maxLines: 4,
                    style: const TextStyle(color: Color(0xFF1A1F2B)),
                    decoration: const InputDecoration(
                      hintText: 'Опишите дефект подробнее...',
                    ),
                  ),
                  const SizedBox(height: 24),

                  // Ошибка
                  if (_error != null) ...[
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: const Color(0xFFFDF2F2),
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(color: const Color(0xFFD64545)),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.error_outline,
                              color: Color(0xFFD64545), size: 18),
                          const SizedBox(width: 8),
                          Text(_error!,
                              style: const TextStyle(
                                  color: Color(0xFFD64545), fontSize: 13)),
                        ],
                      ),
                    ),
                    const SizedBox(height: 16),
                  ],

                  _sending
                      ? const Center(
                          child: CircularProgressIndicator(
                              color: Color(0xFF004F9E)))
                      : ElevatedButton.icon(
                          onPressed: _submit,
                          icon: const Icon(Icons.send),
                          label: const Text('Отправить репорт'),
                        ),
                  const SizedBox(height: 32),
                ],
              ),
            ),
    );
  }
}

// ─── Виджеты ─────────────────────────────────────────────────────────────────

class _SectionLabel extends StatelessWidget {
  final String label;
  const _SectionLabel({required this.label});

  @override
  Widget build(BuildContext context) {
    return Text(
      label,
      style: const TextStyle(
        color: Color(0xFF98A2B3),
        fontSize: 11,
        letterSpacing: 1.5,
        fontWeight: FontWeight.w600,
      ),
    );
  }
}

class _Dropdown extends StatelessWidget {
  final String hint;
  final Map<String, dynamic>? value;
  final List<Map<String, dynamic>> items;
  final String labelKey;
  final void Function(Map<String, dynamic>?)? onChanged;

  const _Dropdown({
    required this.hint,
    required this.value,
    required this.items,
    required this.labelKey,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: const Color(0xFFF8FAFC),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(
          color: onChanged == null
              ? const Color(0xFFEEF2F6)
              : const Color(0xFFD9E2EC),
        ),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<Map<String, dynamic>>(
          value: value,
          isExpanded: true,
          dropdownColor: const Color(0xFFF8FAFC),
          hint: Text(hint,
              style: const TextStyle(color: Color(0xFF98A2B3), fontSize: 14)),
          icon: Icon(
            Icons.keyboard_arrow_down,
            color: onChanged == null
                ? const Color(0xFF98A2B3)
                : const Color(0xFF004F9E),
          ),
          items: items.map((item) {
            return DropdownMenuItem<Map<String, dynamic>>(
              value: item,
              child: Text(
                item[labelKey].toString(),
                style: const TextStyle(color: Color(0xFF1A1F2B), fontSize: 14),
              ),
            );
          }).toList(),
          onChanged: onChanged,
        ),
      ),
    );
  }
}

class _PhotoBlock extends StatelessWidget {
  final File? photo;
  final VoidCallback onCamera;
  final VoidCallback onGallery;

  const _PhotoBlock({
    required this.photo,
    required this.onCamera,
    required this.onGallery,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        if (photo != null) ...[
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: Image.file(photo!,
                height: 200, width: double.infinity, fit: BoxFit.cover),
          ),
          const SizedBox(height: 8),
        ],
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: onCamera,
                icon: const Icon(Icons.camera_alt, size: 18),
                label: Text(photo == null ? 'Камера' : 'Переснять'),
                style: OutlinedButton.styleFrom(
                  foregroundColor: const Color(0xFF004F9E),
                  side: const BorderSide(color: Color(0xFF004F9E)),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(4)),
                ),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: OutlinedButton.icon(
                onPressed: onGallery,
                icon: const Icon(Icons.photo_library, size: 18),
                label: const Text('Галерея'),
                style: OutlinedButton.styleFrom(
                  foregroundColor: const Color(0xFF667085),
                  side: const BorderSide(color: Color(0xFFD9E2EC)),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(4)),
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }
}
