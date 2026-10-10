--[[
The screen a device shows while it waits to be approved on the website: a QR
code a phone can scan (it opens the approval page with the code filled in),
the same code in large type for typing, and a Cancel button. It closes itself
when the plugin reports an outcome; the reader never has to touch it.
]]

local Blitbuffer = require("ffi/blitbuffer")
local Button = require("ui/widget/button")
local CenterContainer = require("ui/widget/container/centercontainer")
local Device = require("device")
local Font = require("ui/font")
local FrameContainer = require("ui/widget/container/framecontainer")
local InputContainer = require("ui/widget/container/inputcontainer")
local QRWidget = require("ui/widget/qrwidget")
local Size = require("ui/size")
local TextBoxWidget = require("ui/widget/textboxwidget")
local TextWidget = require("ui/widget/textwidget")
local UIManager = require("ui/uimanager")
local VerticalGroup = require("ui/widget/verticalgroup")
local VerticalSpan = require("ui/widget/verticalspan")
local T = require("ffi/util").template
local _ = require("gettext")
local Screen = Device.screen

local PairingScreen = InputContainer:extend{
    link = nil,        -- what the QR code opens
    code = nil,        -- XXXX-XXXX
    address = nil,     -- the page to type, for readers without a phone camera
    on_cancel = nil,
    modal = true,
}

function PairingScreen:init()
    local width = math.floor(math.min(Screen:getWidth(), Screen:getHeight()) * 0.86)
    local inner = width - 2 * Size.padding.large
    local qr_size = math.floor(inner * 0.62)

    local group = VerticalGroup:new{
        align = "center",
        TextWidget:new{
            text = _("Connect to your library"),
            face = Font:getFace("tfont", 26),
            bold = true,
        },
        VerticalSpan:new{ width = Size.span.vertical_large * 2 },
        QRWidget:new{ text = self.link, width = qr_size, height = qr_size },
        VerticalSpan:new{ width = Size.span.vertical_large * 2 },
        TextBoxWidget:new{
            text = T(_("Scan this with your phone's camera and tap Approve.\n\nOr open %1 and enter:"),
                self.address),
            face = Font:getFace("cfont", 20),
            width = inner,
            alignment = "center",
        },
        VerticalSpan:new{ width = Size.span.vertical_large },
        TextWidget:new{
            text = self.code,
            face = Font:getFace("tfont", 40),
            bold = true,
        },
        VerticalSpan:new{ width = Size.span.vertical_large },
        TextBoxWidget:new{
            text = _("This screen continues by itself once you approve."),
            face = Font:getFace("cfont", 16),
            width = inner,
            alignment = "center",
        },
        VerticalSpan:new{ width = Size.span.vertical_large * 2 },
        Button:new{
            text = _("Cancel"),
            width = math.floor(inner * 0.5),
            callback = function()
                UIManager:close(self)
                if self.on_cancel then self.on_cancel() end
            end,
        },
    }

    self.frame = FrameContainer:new{
        background = Blitbuffer.COLOR_WHITE,
        radius = Size.radius.window,
        bordersize = Size.border.window,
        padding = Size.padding.large,
        width = width,
        group,
    }
    self[1] = CenterContainer:new{
        dimen = Screen:getSize(),
        self.frame,
    }
end

function PairingScreen:onShow()
    UIManager:setDirty(self, function() return "ui", self.frame.dimen end)
    return true
end

function PairingScreen:onCloseWidget()
    UIManager:setDirty(nil, function() return "ui", self.frame.dimen end)
end

return PairingScreen
