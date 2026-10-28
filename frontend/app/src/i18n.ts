export type Lang = "en" | "ta" | "hi";
export const LANGS: Lang[] = ["en", "ta", "hi"];

const KEY = "aira.lang";

const isLang = (v: string | null | undefined): v is Lang => !!v && (LANGS as string[]).includes(v);

/** ?lang= wins, then the saved choice, then the phone's own language, then English. */
export function loadLang(): Lang {
  const q = new URLSearchParams(location.search).get("lang");
  if (isLang(q)) return q;
  try {
    const v = localStorage.getItem(KEY);
    if (isLang(v)) return v;
  } catch {
    /* storage blocked */
  }
  for (const tag of navigator.languages ?? [navigator.language]) {
    const short = tag?.slice(0, 2).toLowerCase();
    if (isLang(short)) return short;
  }
  return "en";
}

export function saveLang(lang: Lang): void {
  try {
    localStorage.setItem(KEY, lang);
  } catch {
    /* storage blocked */
  }
}

export type Strings = {
  langName: string;
  // Start screen
  tapToStart: string;
  startHint: string;
  language: string;
  languageHint: string;
  talkback: string;
  talkbackHint: string;
  showCamera: string;
  showCameraHint: string;
  hideCamera: string;
  // Device check
  checkTitle: string;
  checkIntro: string;
  micStep: string;
  camStep: string;
  waiting: string;
  checking: string;
  works: string;
  needsAttention: string;
  micSay: string;
  micOk: string;
  micBlocked: string;
  micSilent: string;
  camLook: string;
  camOk: string;
  camDark: string;
  camBlocked: string;
  allGood: string;
  someFailed: string;
  startTalking: string;
  tryAgain: string;
  skipCheck: string;
  // Voice screen
  hint: string;
  you: string;
  aira: string;
  chip: { connecting: string; ready: string; listening: string; thinking: string; speaking: string; reconnecting: string; offline: string; looking: string; counting: string };
  talkOn: string;
  talkOff: string;
  stopped: string;
  connected: string;
  reconnecting: string;
  offline: string;
  mockMode: string;
  micError: string;
  cameraError: string;
  confirmTitle: string;
  confirmHint: string;
  confirmKind: Record<string, string>;
  entity: Record<string, string>;
  count: string;
  counted: (n: number, sku: string, expected: number | null) => string;
  yes: string;
  no: string;
  type: string;
  typePlaceholder: string;
  typeLabel: string;
  send: string;
  buttonLabel: (talking: boolean, confirming: boolean) => string;
};

export const STRINGS: Record<Lang, Strings> = {
  en: {
    langName: "English",
    tapToStart: "Tap to start",
    startHint: "Turns on the microphone and camera.",
    language: "Language",
    languageHint: "AIRA listens and answers in this language.",
    talkback: "I use TalkBack",
    talkbackHint: "AIRA's messages go to TalkBack.",
    showCamera: "Show camera",
    showCameraHint: "A small camera view of what AIRA sees, for a helper.",
    hideCamera: "Hide camera",
    checkTitle: "Checking your microphone and camera",
    checkIntro: "AIRA starts talking once both work.",
    micStep: "Microphone",
    camStep: "Camera",
    waiting: "Waiting",
    checking: "Checking",
    works: "Works",
    needsAttention: "Needs attention",
    micSay: "Checking the microphone. Say: Hello AIRA.",
    micOk: "Microphone works.",
    micBlocked: "The microphone is blocked. Allow the microphone for this site, then try again.",
    micSilent: "I heard nothing. Check the microphone is not muted, then try again.",
    camLook: "Checking the camera. Point the phone at what is in front of you.",
    camOk: "Camera works.",
    camDark: "The picture is too dark. Turn on a light, then try again.",
    camBlocked: "The camera is blocked. Allow the camera for this site, then try again.",
    allGood: "Microphone and camera work. Tap anywhere below to start talking.",
    someFailed: "Something needs attention. Try again, or continue without the check.",
    startTalking: "Start talking",
    tryAgain: "Try again",
    skipCheck: "Continue without the check",
    hint: "Double-tap anywhere to talk. Long-press to stop.",
    you: "You",
    aira: "AIRA",
    chip: { connecting: "Connecting", ready: "Ready", listening: "Listening", thinking: "Thinking", speaking: "Speaking", reconnecting: "Reconnecting", offline: "Offline", looking: "Looking", counting: "Counting" },
    talkOn: "Listening",
    talkOff: "Stopped listening",
    stopped: "Stopped",
    connected: "Connected to AIRA",
    reconnecting: "Connection lost, reconnecting",
    offline: "No internet, waiting",
    mockMode: "Demo",
    micError: "Microphone is not available",
    cameraError: "Camera is not available",
    confirmTitle: "Confirm",
    confirmHint: "Double-tap for yes, long-press for no.",
    confirmKind: { sale: "Confirm sale", payment: "Confirm payment", delivery: "Confirm delivery", stock: "Confirm stock", generic: "Confirm" },
    entity: { order: "Order", delivery: "Delivery", sale: "Sale", payment: "Payment", inventory: "Stock", stock: "Stock" },
    count: "Count",
    counted: (n, sku, e) => (e == null ? `${n} × ${sku}` : `${n} / ${e} ${sku}`),
    yes: "Yes",
    no: "No",
    type: "Type",
    typePlaceholder: "Type a message",
    typeLabel: "Message to AIRA",
    send: "Send",
    buttonLabel: (t, c) =>
      c ? "AIRA is asking. Double-tap for yes. Long-press for no." : t ? "AIRA is listening. Double-tap to stop talking. Long-press to stop AIRA." : "AIRA. Double-tap to talk. Long-press to stop.",
  },
  ta: {
    langName: "தமிழ்",
    tapToStart: "தொடங்க தட்டவும்",
    startHint: "மைக், கேமரா இயக்கப்படும்.",
    language: "மொழி",
    languageHint: "AIRA இந்த மொழியில் கேட்டு பதில் சொல்லும்.",
    talkback: "நான் TalkBack பயன்படுத்துகிறேன்",
    talkbackHint: "AIRA செய்திகளை TalkBack படிக்கும்.",
    showCamera: "கேமராவைக் காட்டு",
    showCameraHint: "AIRA பார்ப்பதை சிறிய கேமரா காட்சியில் காட்டும்; உதவியாளருக்கு.",
    hideCamera: "கேமராவை மறை",
    checkTitle: "மைக் மற்றும் கேமராவைச் சரிபார்க்கிறோம்",
    checkIntro: "இரண்டும் வேலை செய்த பிறகு AIRA பேசத் தொடங்கும்.",
    micStep: "மைக்",
    camStep: "கேமரா",
    waiting: "காத்திருக்கிறது",
    checking: "சரிபார்க்கிறது",
    works: "சரி",
    needsAttention: "சரிசெய்ய வேண்டும்",
    micSay: "மைக் சரிபார்க்கிறேன். ஹலோ AIRA என்று சொல்லுங்கள்.",
    micOk: "மைக் வேலை செய்கிறது.",
    micBlocked: "மைக் தடுக்கப்பட்டுள்ளது. இந்த தளத்திற்கு மைக்கை அனுமதித்து, மீண்டும் முயலுங்கள்.",
    micSilent: "எதுவும் கேட்கவில்லை. மைக் அணைக்கப்படவில்லையா என்று பார்த்து மீண்டும் முயலுங்கள்.",
    camLook: "கேமரா சரிபார்க்கிறேன். உங்கள் முன் உள்ளதை நோக்கி போனைப் பிடியுங்கள்.",
    camOk: "கேமரா வேலை செய்கிறது.",
    camDark: "படம் மிகவும் இருட்டாக உள்ளது. விளக்கைப் போட்டு மீண்டும் முயலுங்கள்.",
    camBlocked: "கேமரா தடுக்கப்பட்டுள்ளது. இந்த தளத்திற்கு கேமராவை அனுமதித்து மீண்டும் முயலுங்கள்.",
    allGood: "மைக்கும் கேமராவும் வேலை செய்கின்றன. பேசத் தொடங்க கீழே தட்டவும்.",
    someFailed: "ஏதோ சரிசெய்ய வேண்டும். மீண்டும் முயலுங்கள், அல்லது சோதனை இல்லாமல் தொடருங்கள்.",
    startTalking: "பேசத் தொடங்கு",
    tryAgain: "மீண்டும் முயல்",
    skipCheck: "சோதனை இல்லாமல் தொடரவும்",
    hint: "பேச எங்கும் இருமுறை தட்டுங்கள். நிறுத்த அழுத்திப் பிடியுங்கள்.",
    you: "நீங்கள்",
    aira: "AIRA",
    chip: { connecting: "இணைக்கிறது", ready: "தயார்", listening: "கேட்கிறேன்", thinking: "யோசிக்கிறேன்", speaking: "பேசுகிறேன்", reconnecting: "மீண்டும் இணைக்கிறது", offline: "இணையம் இல்லை", looking: "பார்க்கிறேன்", counting: "எண்ணுகிறேன்" },
    talkOn: "கேட்கிறேன்",
    talkOff: "கேட்பது நின்றது",
    stopped: "நிறுத்தப்பட்டது",
    connected: "AIRA உடன் இணைந்தது",
    reconnecting: "இணைப்பு போனது, மீண்டும் இணைக்கிறது",
    offline: "இணையம் இல்லை, காத்திருக்கிறது",
    mockMode: "டெமோ",
    micError: "மைக் கிடைக்கவில்லை",
    cameraError: "கேமரா கிடைக்கவில்லை",
    confirmTitle: "உறுதி செய்யவா?",
    confirmHint: "ஆம் என்றால் இருமுறை தட்டுங்கள், இல்லை என்றால் அழுத்திப் பிடியுங்கள்.",
    confirmKind: { sale: "விற்பனை உறுதி செய்யவா?", payment: "பணம் உறுதி செய்யவா?", delivery: "டெலிவரி உறுதி செய்யவா?", stock: "சரக்கு உறுதி செய்யவா?", generic: "உறுதி செய்யவா?" },
    entity: { order: "ஆர்டர்", delivery: "டெலிவரி", sale: "விற்பனை", payment: "பணம்", inventory: "சரக்கு", stock: "சரக்கு" },
    count: "எண்ணிக்கை",
    counted: (n, sku, e) => (e == null ? `${sku}: ${n}` : `${sku}: ${n} / ${e}`),
    yes: "ஆம்",
    no: "இல்லை",
    type: "தட்டச்சு",
    typePlaceholder: "செய்தியை தட்டச்சு செய்யவும்",
    typeLabel: "AIRA-க்கு செய்தி",
    send: "அனுப்பு",
    buttonLabel: (t, c) =>
      c ? "AIRA கேட்கிறது. ஆம் என்றால் இருமுறை தட்டவும். இல்லை என்றால் அழுத்திப் பிடியுங்கள்." : t ? "AIRA கேட்கிறது. நிறுத்த இருமுறை தட்டவும்." : "AIRA. பேச இருமுறை தட்டவும். நிறுத்த அழுத்திப் பிடியுங்கள்.",
  },
  hi: {
    langName: "हिन्दी",
    tapToStart: "शुरू करने के लिए टैप करें",
    startHint: "माइक और कैमरा चालू होंगे।",
    language: "भाषा",
    languageHint: "AIRA इसी भाषा में सुनेगा और जवाब देगा।",
    talkback: "मैं TalkBack इस्तेमाल करता हूँ",
    talkbackHint: "AIRA के संदेश TalkBack पढ़ेगा।",
    showCamera: "कैमरा दिखाएँ",
    showCameraHint: "AIRA जो देखता है, छोटे कैमरा व्यू में; मददगार के लिए।",
    hideCamera: "कैमरा छिपाएँ",
    checkTitle: "माइक और कैमरा जाँच रहे हैं",
    checkIntro: "दोनों ठीक होने पर AIRA बात शुरू करेगा।",
    micStep: "माइक",
    camStep: "कैमरा",
    waiting: "इंतज़ार",
    checking: "जाँच हो रही है",
    works: "ठीक",
    needsAttention: "ध्यान दें",
    micSay: "माइक जाँच रहा हूँ। बोलिए: हैलो AIRA।",
    micOk: "माइक ठीक है।",
    micBlocked: "माइक बंद है। इस साइट के लिए माइक की अनुमति दें, फिर दोबारा कोशिश करें।",
    micSilent: "कुछ सुनाई नहीं दिया। देखें कि माइक म्यूट न हो, फिर दोबारा कोशिश करें।",
    camLook: "कैमरा जाँच रहा हूँ। फ़ोन को अपने सामने की चीज़ की ओर रखें।",
    camOk: "कैमरा ठीक है।",
    camDark: "तस्वीर बहुत अँधेरी है। रोशनी करें, फिर दोबारा कोशिश करें।",
    camBlocked: "कैमरा बंद है। इस साइट के लिए कैमरे की अनुमति दें, फिर दोबारा कोशिश करें।",
    allGood: "माइक और कैमरा ठीक हैं। बात शुरू करने के लिए नीचे टैप करें।",
    someFailed: "कुछ ठीक करना है। दोबारा कोशिश करें, या जाँच के बिना आगे बढ़ें।",
    startTalking: "बात शुरू करें",
    tryAgain: "दोबारा कोशिश करें",
    skipCheck: "जाँच के बिना आगे बढ़ें",
    hint: "बोलने के लिए कहीं भी दो बार टैप करें। रोकने के लिए दबाकर रखें।",
    you: "आप",
    aira: "AIRA",
    chip: { connecting: "जुड़ रहा है", ready: "तैयार", listening: "सुन रहा हूँ", thinking: "सोच रहा हूँ", speaking: "बोल रहा हूँ", reconnecting: "फिर से जुड़ रहा है", offline: "ऑफ़लाइन", looking: "देख रहा हूँ", counting: "गिन रहा हूँ" },
    talkOn: "सुन रहा हूँ",
    talkOff: "सुनना बंद",
    stopped: "रोका गया",
    connected: "AIRA से जुड़ गया",
    reconnecting: "कनेक्शन टूटा, फिर से जुड़ रहा है",
    offline: "इंटरनेट नहीं है, इंतज़ार",
    mockMode: "डेमो",
    micError: "माइक उपलब्ध नहीं है",
    cameraError: "कैमरा उपलब्ध नहीं है",
    confirmTitle: "पक्का करें?",
    confirmHint: "हाँ के लिए दो बार टैप, ना के लिए दबाकर रखें।",
    confirmKind: { sale: "बिक्री पक्की करें?", payment: "भुगतान पक्का करें?", delivery: "डिलीवरी पक्की करें?", stock: "स्टॉक पक्का करें?", generic: "पक्का करें?" },
    entity: { order: "ऑर्डर", delivery: "डिलीवरी", sale: "बिक्री", payment: "भुगतान", inventory: "स्टॉक", stock: "स्टॉक" },
    count: "गिनती",
    counted: (n, sku, e) => (e == null ? `${sku}: ${n}` : `${sku}: ${n} / ${e}`),
    yes: "हाँ",
    no: "नहीं",
    type: "टाइप",
    typePlaceholder: "संदेश टाइप करें",
    typeLabel: "AIRA को संदेश",
    send: "भेजें",
    buttonLabel: (t, c) =>
      c ? "AIRA पूछ रहा है। हाँ के लिए दो बार टैप करें। ना के लिए दबाकर रखें।" : t ? "AIRA सुन रहा है। बोलना बंद करने के लिए दो बार टैप करें।" : "AIRA. बोलने के लिए दो बार टैप करें। रोकने के लिए दबाकर रखें।",
  },
};
