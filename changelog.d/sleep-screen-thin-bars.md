### Fixed

- **KOReader sleep screens no longer show thin white bands when the image is nearly the screen's shape.** An image that would leave a thin band above and below (or at the sides) is now zoomed just enough to fill the screen, cropping evenly and never stretching. An image far from the screen's shape, where a zoom would cut off more than a tenth of it, is still shown whole on white. Sleep screens set before this update are redone on the device's next sync, with no re-upload; a device whose image comes out the same downloads nothing new.
