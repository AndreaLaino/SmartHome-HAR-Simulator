import Foundation
import AppKit
import CoreGraphics
import PDFKit

// Native PDF slide authoring. Text and tables remain selectable in the PDF.
let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let data = try Data(contentsOf: root.appendingPathComponent("presentazione_har_contenuti.json"))
let slides = try JSONSerialization.jsonObject(with: data) as! [[String: Any]]
let output = root.appendingPathComponent("Guida_visiva_HAR_e_prove.pdf")
let W: CGFloat = 1280
let H: CGFloat = 720
let margin: CGFloat = 64
let navy = NSColor(calibratedRed: 0.07, green: 0.15, blue: 0.22, alpha: 1)
let teal = NSColor(calibratedRed: 0.03, green: 0.38, blue: 0.39, alpha: 1)
let orange = NSColor(calibratedRed: 0.75, green: 0.29, blue: 0.09, alpha: 1)
let muted = NSColor(calibratedRed: 0.31, green: 0.37, blue: 0.40, alpha: 1)
let paper = NSColor(calibratedRed: 0.985, green: 0.979, blue: 0.958, alpha: 1)
let pale = NSColor(calibratedRed: 0.91, green: 0.95, blue: 0.94, alpha: 1)
let hairline = NSColor(calibratedRed: 0.78, green: 0.82, blue: 0.81, alpha: 1)
var issues: [String] = []
var currentPage = 0

func fill(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, _ color: NSColor) {
    color.setFill()
    NSBezierPath(rect: NSRect(x: x, y: H-y-h, width: w, height: h)).fill()
}

func attributes(_ size: CGFloat, _ weight: NSFont.Weight, _ color: NSColor) -> [NSAttributedString.Key: Any] {
    let style = NSMutableParagraphStyle()
    style.lineSpacing = size * 0.11
    style.paragraphSpacing = size * 0.16
    style.lineBreakMode = .byWordWrapping
    return [.font: NSFont.systemFont(ofSize: size, weight: weight),
            .foregroundColor: color, .paragraphStyle: style]
}

@discardableResult
func text(_ value: String, _ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat,
          _ size: CGFloat = 25, _ weight: NSFont.Weight = .regular,
          _ color: NSColor? = nil) -> CGFloat {
    let attr = NSAttributedString(string: value, attributes: attributes(size, weight, color ?? navy))
    let needed = ceil(attr.boundingRect(with: NSSize(width: w, height: 2000),
                                       options: [.usesLineFragmentOrigin, .usesFontLeading]).height)
    if needed > h + 1 { issues.append("Slide \(currentPage): text overflow \(Int(needed)) > \(Int(h)): \(value.prefix(65))") }
    attr.draw(with: NSRect(x: x, y: H-y-h, width: w, height: h),
              options: [.usesLineFragmentOrigin, .usesFontLeading])
    return needed
}

func measure(_ value: String, _ width: CGFloat, _ size: CGFloat) -> CGFloat {
    let attr = NSAttributedString(string: value, attributes: attributes(size, .regular, navy))
    return ceil(attr.boundingRect(with: NSSize(width: width, height: 2000),
                                 options: [.usesLineFragmentOrigin, .usesFontLeading]).height)
}

func picture(_ path: String, _ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat) {
    guard let img = NSImage(contentsOf: root.appendingPathComponent(path)) else {
        issues.append("Slide \(currentPage): missing image \(path)"); return
    }
    let scale = min(w / img.size.width, h / img.size.height)
    let iw = img.size.width * scale
    let ih = img.size.height * scale
    img.draw(in: NSRect(x: x+(w-iw)/2, y: H-y-(h+ih)/2, width: iw, height: ih),
             from: .zero, operation: .sourceOver, fraction: 1)
}

var box = CGRect(x: 0, y: 0, width: W, height: H)
let info: [CFString: Any] = [
    kCGPDFContextTitle: "Attività, proprietà e trasferimento - Guida visiva HAR",
    kCGPDFContextAuthor: "SmartHome-HAR-Simulator",
    kCGPDFContextSubject: "Guida in italiano con illustrazioni e prove passo per passo",
    kCGPDFContextCreator: "Native PDF slide builder"
]
guard let pdf = CGContext(output as CFURL, mediaBox: &box, info as CFDictionary) else {
    fatalError("Cannot create PDF")
}

for (index, slide) in slides.enumerated() {
    currentPage = index + 1
    pdf.beginPDFPage(nil)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(cgContext: pdf, flipped: false)
    fill(0, 0, W, H, paper)
    let layout = slide["layout"] as! String
    text(slide["section"] as! String, margin, 31, 1130, 26, 17, .semibold, teal)
    text(slide["title"] as! String, margin, 76, 1152, index == 0 ? 150 : 76,
         index == 0 ? 52 : 43, .bold)

    if layout == "hero" {
        let start: CGFloat = index == 0 ? 248 : 189
        text(slide["lead"] as! String, margin, start, 438, 96, 31, .semibold, teal)
        text(slide["body"] as! String, margin, start+111, 438, index == 0 ? 190 : 287, 25)
        picture(slide["image"] as! String, 548, index == 0 ? 226 : 190, 668, 376)
    } else if layout == "columns" {
        let columns = slide["columns"] as! [[String: String]]
        fill(629, 187, 1, 365, hairline)
        for (i, col) in columns.enumerated() {
            let x: CGFloat = i == 0 ? margin : 674
            let cw: CGFloat = i == 0 ? 522 : 542
            text(col["label"]!, x, 185, cw, 24, 15, .bold, orange)
            text(col["heading"]!, x, 224, cw, 83, 31, .semibold)
            text(col["body"]!, x, 306, cw, 285, 25)
        }
    } else if layout == "table" {
        let headers = slide["headers"] as! [String]
        let widths = (slide["widths"] as! [Double]).map { CGFloat($0) * 1152 }
        let rows = slide["rows"] as! [[String]]
        let cellPad: CGFloat = 17
        var x = margin
        fill(margin, 180, 1152, 49, teal)
        for i in headers.indices {
            text(headers[i], x+cellPad, 190, widths[i]-cellPad*2, 30, 22, .semibold, .white)
            x += widths[i]
        }
        var y: CGFloat = 229
        for (r, row) in rows.enumerated() {
            let heights = row.enumerated().map { measure($0.element, widths[$0.offset]-cellPad*2, 23) }
            let rh = max(76, (heights.max() ?? 30)+26)
            if r % 2 == 0 { fill(margin, y, 1152, rh, .white) }
            var cx = margin
            for i in row.indices {
                text(row[i], cx+cellPad, y+13, widths[i]-cellPad*2, rh-24, 23,
                     i == 0 ? .medium : .regular)
                cx += widths[i]
            }
            fill(margin, y+rh-1, 1152, 1, hairline)
            y += rh
        }
        if let after = slide["after"] as? String {
            text(after, margin+2, y+25, 1148, 590-y-25, 25, .medium, teal)
        }
        if y > 590 { issues.append("Slide \(currentPage): table exceeds body area: \(y)") }
    } else if layout == "steps" {
        let steps = slide["steps"] as! [[String: String]]
        let hasPath = slide["path"] != nil
        let dy: CGFloat = steps.count == 4 ? 101 : (hasPath ? 101 : 128)
        for (i, step) in steps.enumerated() {
            let y = 181 + CGFloat(i)*dy
            text(String(format: "%02d", i+1), margin, y-2, 60, 47, 32, .bold, teal)
            text(step["title"]!, margin+79, y, 1073, 37, 25, .semibold)
            text(step["body"]!, margin+79, y+42, 1073, 58, 23)
        }
        if let path = slide["path"] as? String {
            text("PERCORSO DA INCOLLARE", margin+79, 503, 1050, 23, 15, .bold, teal)
            text(path, margin+79, 535, 1060, 58, 22, .medium)
        }
    } else if layout == "plain" {
        for (i, b) in (slide["blocks"] as! [[String: String]]).enumerated() {
            let y = 181 + CGFloat(i)*103
            text(b["heading"]!, margin, y, 1152, 33, 25, .semibold, teal)
            text(b["body"]!, margin, y+39, 1152, 58, 22)
        }
    }

    fill(margin, 618, 1152, 59, pale)
    text(slide["takeaway"] as! String, margin+15, 626, 1122, 47, 20, .medium)
    text(slide["source"] as! String, margin, 690, 1053, 22, 12, .regular, muted)
    text("\(index+1) / \(slides.count)", 1139, 688, 77, 24, 14, .semibold, muted)
    NSGraphicsContext.restoreGraphicsState()
    pdf.endPDFPage()
}
pdf.closePDF()

guard let check = PDFDocument(url: output), check.pageCount == slides.count else {
    fatalError("PDF validation failed")
}
var extracted = ""
for i in 0..<check.pageCount {
    extracted += "\n\nPAGE \(i+1)\n" + (check.page(at: i)?.string ?? "")
}
try extracted.write(to: root.appendingPathComponent("presentazione_testo_verificato.txt"), atomically: true, encoding: .utf8)
let validation: [String: Any] = ["pages": check.pageCount, "issues": issues,
                                 "output": output.path, "textCharacters": extracted.count]
let validationData = try JSONSerialization.data(withJSONObject: validation, options: [.prettyPrinted, .sortedKeys])
try validationData.write(to: root.appendingPathComponent("verifica_presentazione.json"))
print(String(data: validationData, encoding: .utf8)!)
if !issues.isEmpty { exit(2) }
