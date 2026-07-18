import Foundation
import Security

/// Minimal Keychain wrapper for storing API keys as generic passwords
/// under the service "com.championsgroup.champstudio".
enum Keychain {
    static let service = "com.championsgroup.champstudio"

    private static func baseQuery(_ account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
    }

    /// Read a stored value; nil if not present.
    static func get(_ account: String) -> String? {
        var query = baseQuery(account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8) else {
            return nil
        }
        return value
    }

    /// Store (add or update) a value. Empty string deletes the item.
    @discardableResult
    static func set(_ value: String, for account: String) -> Bool {
        guard !value.isEmpty else { return delete(account) }
        let data = Data(value.utf8)

        // Try update first.
        let updateStatus = SecItemUpdate(
            baseQuery(account) as CFDictionary,
            [kSecValueData as String: data] as CFDictionary
        )
        if updateStatus == errSecSuccess { return true }

        // Not found -> add.
        var addQuery = baseQuery(account)
        addQuery[kSecValueData as String] = data
        return SecItemAdd(addQuery as CFDictionary, nil) == errSecSuccess
    }

    @discardableResult
    static func delete(_ account: String) -> Bool {
        let status = SecItemDelete(baseQuery(account) as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }
}
