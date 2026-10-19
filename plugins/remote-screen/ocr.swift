// remote-screen OCR helper: `ocr <image>` prints JSON lines found by Apple's Vision framework:
// [{"text": "...", "conf": 0.98, "x": .., "y": .., "w": .., "h": ..}] in image pixels, top-left origin.
// Wide screens are read in overlapping tiles (regionOfInterest): on a 3440px frame Vision otherwise
// downsamples and drops small dialog text.
import AppKit
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count >= 2, let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("usage: ocr <image> [tile_px]\n".data(using: .utf8)!)
    exit(2)
}
let W = Double(cg.width), H = Double(cg.height)
let tile = args.count >= 3 ? (Double(args[2]) ?? 1100) : 1100
let overlap = 200.0

struct Line { var text: String; var conf: Double; var x: Double; var y: Double; var w: Double; var h: Double }
var lines: [Line] = []

func recognize(_ roi: CGRect) {  // roi in normalized, bottom-left coords
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false
    req.regionOfInterest = roi
    do { try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req]) } catch { return }
    for obs in req.results ?? [] {
        guard let cand = obs.topCandidates(1).first else { continue }
        let b = obs.boundingBox  // normalized within roi
        let nx = roi.minX + b.minX * roi.width, ny = roi.minY + b.minY * roi.height
        let nw = b.width * roi.width, nh = b.height * roi.height
        lines.append(Line(text: cand.string, conf: Double(cand.confidence),
                          x: nx * W, y: (1 - ny - nh) * H, w: nw * W, h: nh * H))
    }
}

if W <= tile * 1.3 && H <= tile * 1.3 {
    recognize(CGRect(x: 0, y: 0, width: 1, height: 1))
} else {
    var ty = 0.0
    while true {
        var tx = 0.0
        let th = min(tile, H - ty)
        while true {
            let tw = min(tile, W - tx)
            recognize(CGRect(x: tx / W, y: 1 - (ty + th) / H, width: tw / W, height: th / H))
            if tx + tw >= W { break }
            tx += tile - overlap
        }
        if ty + th >= H { break }
        ty += tile - overlap
    }
}

// Dedupe tile-overlap duplicates: drop a line mostly covered by a longer one containing the same text.
func inter(_ a: Line, _ b: Line) -> Double {
    let w = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    let h = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    return w * h
}
lines.sort { $0.text.count > $1.text.count }
var kept: [Line] = []
for l in lines {
    let area = max(1, l.w * l.h)
    let t = l.text.lowercased()
    if kept.contains(where: { inter($0, l) / area > 0.6 && $0.text.lowercased().contains(t) }) { continue }
    kept.append(l)
}
kept.sort { ($0.y, $0.x) < ($1.y, $1.x) }
let out: [[String: Any]] = kept.map {
    ["text": $0.text, "conf": $0.conf, "x": Int($0.x.rounded()), "y": Int($0.y.rounded()),
     "w": Int($0.w.rounded()), "h": Int($0.h.rounded())]
}
let data = try JSONSerialization.data(withJSONObject: out)
FileHandle.standardOutput.write(data)
