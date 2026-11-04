// 用 WKWebView（與 Safari 同一套列印流程）把網頁印成 PDF，可指定最小字級，重現使用者的 Safari 列印結果
import Cocoa
import WebKit

let args = CommandLine.arguments
guard args.count >= 3 else { print("usage: wkprint <file-url-or-path[#hash]> <out.pdf> [minFont] [js-before-print]"); exit(2) }
let minFont = args.count > 3 ? CGFloat(Double(args[3]) ?? 0) : 0
let jsBefore = args.count > 4 ? args[4] : ""
let outURL = URL(fileURLWithPath: args[2])
let jsAfter = args.count > 5 ? args[5] : ""

final class Runner: NSObject, WKNavigationDelegate {
  var web: WKWebView!
  var win: NSWindow!
  func start() {
    let cfg = WKWebViewConfiguration()
    cfg.preferences.minimumFontSize = minFont
    web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1200, height: 900), configuration: cfg)
    web.navigationDelegate = self
    win = NSWindow(contentRect: NSRect(x: -3000, y: -3000, width: 1200, height: 900), styleMask: [.borderless], backing: .buffered, defer: false)
    win.contentView = web
    win.orderBack(nil)
    var s = args[1]; var frag: String? = nil
    if let i = s.firstIndex(of: "#") { frag = String(s[s.index(after: i)...]); s = String(s[..<i]) }
    let fileURL = URL(fileURLWithPath: s)
    var comps = URLComponents(url: fileURL, resolvingAgainstBaseURL: false)!
    comps.fragment = frag
    web.loadFileURL(comps.url!, allowingReadAccessTo: fileURL.deletingLastPathComponent())
  }
  func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
      if jsBefore.isEmpty { self.doPrint() }
      else { webView.evaluateJavaScript(jsBefore) { r, e in
        if let e = e { print("js error:", e) } else if let r = r { print("js:", r) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { self.doPrint() } } }
    }
  }
  func doPrint() {
    let pi = NSPrintInfo(dictionary: [:])
    pi.paperSize = NSSize(width: 595.28, height: 841.89)
    pi.orientation = .portrait
    pi.topMargin = 0; pi.bottomMargin = 0; pi.leftMargin = 0; pi.rightMargin = 0
    pi.horizontalPagination = .automatic; pi.verticalPagination = .automatic
    pi.isHorizontallyCentered = false; pi.isVerticallyCentered = false
    pi.jobDisposition = .save
    pi.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = outURL
    let op = web.printOperation(with: pi)
    op.showsPrintPanel = false; op.showsProgressPanel = false
    op.view?.frame = NSRect(x: 0, y: 0, width: 595.28, height: 841.89)
    op.runModal(for: win, delegate: self, didRun: #selector(done(_:success:contextInfo:)), contextInfo: nil)
  }
  @objc func done(_ op: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
    print(success ? "ok" : "print failed")
    if jsAfter.isEmpty { exit(success ? 0 : 1) }
    web.evaluateJavaScript(jsAfter) { r, e in
      if let e = e { print("js error:", e) } else if let r = r { print("after:", r) }
      exit(success ? 0 : 1) }
  }
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let r = Runner()
r.start()
DispatchQueue.main.asyncAfter(deadline: .now() + 60) { print("timeout"); exit(3) }
app.run()
