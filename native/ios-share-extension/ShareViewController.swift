// LinkVault Share Extension.
// Receives a URL (or text containing one) from the iOS share sheet and POSTs it to
// the LinkVault backend with your API key. The server does the rest.
//
// Configure two keys in the extension's Info.plist:
//   LVServerURL  e.g. https://linkvault.up.railway.app
//   LVApiKey     from LinkVault > Settings > API key

import UIKit
import UniformTypeIdentifiers

final class ShareViewController: UIViewController {
    private let label = UILabel()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor.black.withAlphaComponent(0.35)
        label.text = "Saving to LinkVault…"
        label.textColor = .label
        label.backgroundColor = .systemBackground
        label.textAlignment = .center
        label.layer.cornerRadius = 14
        label.clipsToBounds = true
        label.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(label)
        NSLayoutConstraint.activate([
            label.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            label.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            label.widthAnchor.constraint(equalToConstant: 240),
            label.heightAnchor.constraint(equalToConstant: 64),
        ])
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        Task { await handleShare() }
    }

    private func handleShare() async {
        guard let text = await sharedText() else {
            return finish("No link found", success: false)
        }
        do {
            try await post(text)
            finish("Saved ✓", success: true)
        } catch {
            finish("Couldn't save: \(error.localizedDescription)", success: false)
        }
    }

    /// First URL attachment, otherwise the shared plain text (the server pulls the link out of it).
    private func sharedText() async -> String? {
        let items = (extensionContext?.inputItems as? [NSExtensionItem]) ?? []
        let providers = items.flatMap { $0.attachments ?? [] }
        for p in providers where p.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
            if let url = try? await p.loadItem(forTypeIdentifier: UTType.url.identifier) as? URL {
                return url.absoluteString
            }
        }
        for p in providers where p.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
            if let s = try? await p.loadItem(forTypeIdentifier: UTType.plainText.identifier) as? String {
                return s
            }
        }
        return nil
    }

    private func post(_ text: String) async throws {
        let info = Bundle.main.infoDictionary ?? [:]
        guard let server = info["LVServerURL"] as? String, let key = info["LVApiKey"] as? String,
              let endpoint = URL(string: server.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/bookmarks")
        else { throw NSError(domain: "LinkVault", code: 1, userInfo: [NSLocalizedDescriptionKey: "Set LVServerURL and LVApiKey"]) }

        var req = URLRequest(url: endpoint, timeoutInterval: 15)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(key, forHTTPHeaderField: "X-API-Key")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["url": text])
        let (_, response) = try await URLSession.shared.data(for: req)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw NSError(domain: "LinkVault", code: status, userInfo: [NSLocalizedDescriptionKey: "Server returned \(status)"])
        }
    }

    private func finish(_ message: String, success: Bool) {
        Task { @MainActor in
            label.text = message
            try? await Task.sleep(nanoseconds: success ? 700_000_000 : 2_000_000_000)
            extensionContext?.completeRequest(returningItems: nil)
        }
    }
}
