#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
@interface CGVirtualDisplayMode : NSObject
- (instancetype)initWithWidth:(unsigned int)w height:(unsigned int)h refreshRate:(double)r;
@end
@interface CGVirtualDisplaySettings : NSObject
@property (retain) NSArray *modes;
@property unsigned int hiDPI;
@end
@interface CGVirtualDisplayDescriptor : NSObject
@property (retain) dispatch_queue_t queue;
@property (retain) NSString *name;
@property unsigned int maxPixelsWide, maxPixelsHigh, vendorID, productID, serialNum;
@property CGSize sizeInMillimeters;
@property (copy) void (^terminationHandler)(id, id);
@end
@interface CGVirtualDisplay : NSObject
@property (readonly) CGDirectDisplayID displayID;
- (instancetype)initWithDescriptor:(CGVirtualDisplayDescriptor *)d;
- (BOOL)applySettings:(CGVirtualDisplaySettings *)s;
@end
