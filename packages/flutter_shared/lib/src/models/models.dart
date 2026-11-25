/// Placeholder models; real shapes arrive with the data-model tickets (E02).
enum UserRole { parent, teacher, admin }

class UserProfile {
  const UserProfile({required this.id, required this.name, required this.role});
  final String id;
  final String name;
  final UserRole role;
}
