import com.google.zxing.BarcodeFormat;
import com.google.zxing.qrcode.QRCodeWriter;
import java.awt.image.BufferedImage;
import java.nio.file.Files;
import java.nio.file.Path;
import javax.imageio.ImageIO;

/** Generate the physical-camera test fixture using the same ZXing core as the app. */
class GenerateDemoQr {
    public static void main(String[] args) throws Exception {
        var target = Path.of(args[0]);
        Files.createDirectories(target.toAbsolutePath().getParent());
        var matrix = new QRCodeWriter().encode("https://example.org/", BarcodeFormat.QR_CODE, 800, 800);
        var image = new BufferedImage(800, 800, BufferedImage.TYPE_INT_RGB);
        for (int y = 0; y < 800; y++)
            for (int x = 0; x < 800; x++)
                image.setRGB(x, y, matrix.get(x, y) ? 0 : 0xffffff);
        ImageIO.write(image, "png", target.toFile());
        System.out.println("QR fixture: https://example.org/ -> " + target);
    }
}
