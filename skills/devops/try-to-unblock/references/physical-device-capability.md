# Qualifying an existing physical-device route

Use for bounded read-only capability discovery when device acceptance is held. Discovery does not release the original test.

1. Read original acceptance, current owner disposition and pending helper receipts. Reuse the local capability owner before filing a human-only phone ask: a running discovery task may still invalidate its premise.
2. Discover the authenticated Desktop gateway and perform authorized local reads on the paired-device Mac. SSH reachability and local device availability are different predicates; preserve remote transport restrictions.
3. Compare `xcrun devicectl list devices` with installed `pymobiledevice3 usbmux list` for the exact physical target. Wi-Fi lockdown can succeed intermittently while CoreDevice reports unavailable. Record observations and times separately. In a read-only discovery scope, leave pairing, unlock and Developer Mode unchanged.
4. Query installed app metadata via the existing route, e.g. `pymobiledevice3 apps query <bundle-id> --udid <exact-target>`. A successful version read proves metadata access only. Trace the actual fix commit into the installed release separately; a parent's executor repair is not feature-fix provenance.
5. Qualify services separately. Existing lockdown with `create_using_usbmux(serial=UDID, autopair=False)` and `AccessibilityAudit.capabilities()` has demonstrated developer-service reachability without UI inspection. Capabilities do not establish foreground/unlocked state, geometry or behavior. For exact-device acceptance, exclude other paired phones and simulators from the proof.
6. Inspect installed API implementations and exercise serializers with fake transports before proposing input. Accessibility Activate bypasses coordinate hit testing, so it cannot prove that empty row space is inert. Inspect implicit observation effects: element discovery can move inspector focus, and a touch session can start display-video transmission even when frames are discarded. Classify that as screen access, not metadata.
7. Return the bounded method and missing prerequisites to the existing acceptance owner. Fake-adapter tests prove protocol/safety behavior, not physical acceptance. When the original authorization excludes UI inspection or streaming, obtain a scoped authorization before that real-device phase; a successful capabilities call does not grant it.

## Input-method qualification

Before authorized physical execution, validate exact target/bundle/physical identity, fresh foreground/unlocked state, orientation and geometry, and a bounded action count. Distinguish CONTACT/RELEASE from AX activation. Exercise interruption between contact and release, unknown outcomes, durable journal recovery and cancellation. For ambiguous physical outcomes, stop automatic replay and report the final state unknown rather than blindly toggling to restore it.

The demonstrated workflow ends at metadata/service discovery and offline qualification. It does not establish a working end-to-end remote phone tap adapter.
