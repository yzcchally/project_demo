// Offline text grounding for screenshot controls omitted from the UI tree.
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <ImageIO/ImageIO.h>
int main(int argc, const char *argv[]) {
  @autoreleasepool {
    if (argc != 2) return 2;
    NSURL *url = [NSURL fileURLWithPath:@(argv[1])];
    CGImageSourceRef source = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
    if (!source) return 2;
    CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, NULL);
    CFRelease(source);
    if (!image) return 2;
    NSUInteger width = CGImageGetWidth(image), height = CGImageGetHeight(image);
    VNRecognizeTextRequest *request = [VNRecognizeTextRequest new];
    request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
    request.recognitionLanguages = @[@"zh-Hans", @"en-US"];
    request.usesLanguageCorrection = NO;
    NSError *error = nil;
    VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
    BOOL ok = [handler performRequests:@[request] error:&error];
    CGImageRelease(image);
    if (!ok) return 3;
    NSMutableArray *items = [NSMutableArray new];
    for (VNRecognizedTextObservation *observation in request.results) {
      VNRecognizedText *text = [observation topCandidates:1].firstObject;
      if (!text) continue;
      CGRect r = observation.boundingBox;
      [items addObject:@{@"text":text.string, @"confidence":@(text.confidence),
        @"bounds":@[@((int)(r.origin.x*width)), @((int)((1-CGRectGetMaxY(r))*height)),
                    @((int)(CGRectGetMaxX(r)*width)), @((int)((1-r.origin.y)*height))]}];
    }
    NSData *data = [NSJSONSerialization dataWithJSONObject:@{@"width":@(width), @"height":@(height), @"items":items} options:0 error:&error];
    if (!data) return 4;
    [[NSFileHandle fileHandleWithStandardOutput] writeData:data];
  }
  return 0;
}
