"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const localMode = location.protocol === "http:" && location.hostname !== "localhost";
  const enc = new TextEncoder();
  const bytes = value => typeof value === "string" ? enc.encode(value) : value;
  const hex = input => Array.from(input, x => x.toString(16).padStart(2, "0")).join("");
  const fromHex = value => Uint8Array.from(value.match(/.{2}/g) || [], h => parseInt(h, 16));
  const concat = (...parts) => {
    const result = new Uint8Array(parts.reduce((n, part) => n + part.length, 0));
    let pos = 0;
    for (const part of parts) { result.set(part, pos); pos += part.length; }
    return result;
  };
  const random = count => {
    if (!globalThis.crypto || !crypto.getRandomValues) throw Error("This browser does not provide secure random numbers.");
    const result = new Uint8Array(count);
    crypto.getRandomValues(result);
    return result;
  };
  const K = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
  ];
  const rotate = (v,n) => (v >>> n) | (v << (32 - n));
  // SHA-256 and HMAC use the identical bytes and signed-message format as Work Status.
  // Implemented synchronously because the badge's HTTP origin cannot use WebCrypto subtle.
  function sha256(input) {
    const message = bytes(input);
    const paddedLength = Math.ceil((message.length + 9) / 64) * 64;
    const padded = new Uint8Array(paddedLength);
    padded.set(message); padded[message.length] = 0x80;
    const view = new DataView(padded.buffer);
    const bits = BigInt(message.length) * 8n;
    view.setUint32(paddedLength - 8, Number(bits >> 32n) >>> 0);
    view.setUint32(paddedLength - 4, Number(bits & 0xffffffffn) >>> 0);
    const h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const w = new Uint32Array(64);
    for (let start = 0; start < paddedLength; start += 64) {
      for (let i = 0; i < 16; i++) w[i] = view.getUint32(start + i * 4);
      for (let i = 16; i < 64; i++) {
        const a = w[i - 15], b = w[i - 2];
        const s0 = rotate(a,7) ^ rotate(a,18) ^ (a >>> 3);
        const s1 = rotate(b,17) ^ rotate(b,19) ^ (b >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let [a,b,c,d,e,f,g,hh] = h;
      for (let i = 0; i < 64; i++) {
        const s1 = rotate(e,6) ^ rotate(e,11) ^ rotate(e,25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (hh + s1 + ch + K[i] + w[i]) >>> 0;
        const s0 = rotate(a,2) ^ rotate(a,13) ^ rotate(a,22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (s0 + maj) >>> 0;
        hh = g; g = f; f = e; e = (d + t1) >>> 0;
        d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      for (const [i,value] of [a,b,c,d,e,f,g,hh].entries()) h[i] = (h[i] + value) >>> 0;
    }
    const result = new Uint8Array(32), output = new DataView(result.buffer);
    h.forEach((word,i) => output.setUint32(i * 4,word));
    return result;
  }
  function hmac(key, message) {
    let secret = key.length > 64 ? sha256(key) : key;
    const a = new Uint8Array(64), b = new Uint8Array(64);
    for (let i = 0; i < 64; i++) { a[i] = (secret[i] || 0) ^ 0x36; b[i] = (secret[i] || 0) ^ 0x5c; }
    return sha256(concat(b,sha256(concat(a,bytes(message)))));
  }
  const P = (1n << 255n) - 19n, A24 = 121665n;
  const mod = value => ((value % P) + P) % P;
  const leNumber = input => {
    let result = 0n;
    for (let i = input.length - 1; i >= 0; i--) result = (result << 8n) + BigInt(input[i]);
    return result;
  };
  const leBytes = (input,count) => {
    const result = new Uint8Array(count); let number = input;
    for (let i = 0; i < count; i++) { result[i] = Number(number & 255n); number >>= 8n; }
    return result;
  };
  function powMod(base,exponent) {
    let result = 1n, factor = mod(base), power = exponent;
    while (power > 0n) {
      if (power & 1n) result = mod(result * factor);
      factor = mod(factor * factor); power >>= 1n;
    }
    return result;
  }
  // RFC 7748 ladder, matching the x25519() function in Work Status.
  function x25519(privateBytes,peerBytes) {
    const scalarBytes = privateBytes.slice();
    scalarBytes[0] &= 248; scalarBytes[31] &= 127; scalarBytes[31] |= 64;
    const scalar = leNumber(scalarBytes), x1 = leNumber(peerBytes) % P;
    let x2=1n,z2=0n,x3=x1,z3=1n,swap=0n;
    for (let i=254;i>=0;i--) {
      const bit = (scalar >> BigInt(i)) & 1n; swap ^= bit;
      if (swap) { [x2,x3]=[x3,x2]; [z2,z3]=[z3,z2]; }
      swap=bit;
      const a=mod(x2+z2),aa=mod(a*a),b=mod(x2-z2),bb=mod(b*b),e=mod(aa-bb);
      const c=mod(x3+z3),d=mod(x3-z3),da=mod(d*a),cb=mod(c*b);
      x3=mod((da+cb)*(da+cb)); z3=mod(x1*(da-cb)*(da-cb));
      x2=mod(aa*bb); z2=mod(e*(aa+A24*e));
    }
    if (swap) { [x2,x3]=[x3,x2]; [z2,z3]=[z3,z2]; }
    return leBytes(mod(x2*powMod(z2,P-2n)),32);
  }
  function timingEqual(a,b) {
    if (a.length !== b.length) return false;
    let difference=0; for (let i=0;i<a.length;i++) difference |= a[i]^b[i];
    return difference === 0;
  }
  const feedback=(message,type="")=>{ $("feedback").textContent=message; $("feedback").className="feedback "+type; };
  const delay=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
  const host=location.host;
  const storeId="photo-frame-device:"+host,storeKey="photo-frame-secret:"+host;
  let deviceId=localStorage.getItem(storeId),keyHex=localStorage.getItem(storeKey);
  let selectedFile=null,bitmap=null,zoom=1,panX=0,panY=0,drag=null,busy=false;
  const canvas=$("preview"),ctx=canvas.getContext("2d",{willReadFrequently:false});
  function renderPhoto() {
    ctx.fillStyle="#14213a"; ctx.fillRect(0,0,160,120);
    if (!bitmap) return;
    const base=Math.max(160/bitmap.width,120/bitmap.height);
    const w=bitmap.width*base*zoom,h=bitmap.height*base*zoom;
    panX=Math.max(-(w-160)/2,Math.min((w-160)/2,panX));
    panY=Math.max(-(h-120)/2,Math.min((h-120)/2,panY));
    ctx.drawImage(bitmap,(160-w)/2+panX,(120-h)/2+panY,w,h);
  }
  function updateSend() { $("send").disabled=busy || !bitmap || !keyHex; }
  async function onFile(file) {
    if (!file) return;
    if (!file.type.startsWith("image/")) throw Error("Please choose an image file.");
    let next;
    try { next=await createImageBitmap(file,{imageOrientation:"from-image"}); }
    catch (error) {
      next=await new Promise((resolve,reject)=>{
        const image=new Image(),url=URL.createObjectURL(file);
        image.onload=()=>{URL.revokeObjectURL(url);resolve(image);};
        image.onerror=()=>{URL.revokeObjectURL(url);reject(Error("This image could not be opened."));};
        image.src=url;
      });
    }
    if (bitmap && bitmap.close) bitmap.close();
    bitmap=next; selectedFile=file; zoom=1;panX=0;panY=0;
    $("zoom").disabled=false;$("zoom").value="1";$("zoomValue").textContent="1.0×";
    $("empty").hidden=true;$("photoName").textContent=file.name;
    $("photoInfo").textContent=bitmap.width+" × "+bitmap.height+" source · cropped to badge";
    renderPhoto();updateSend();feedback("Photo ready. Adjust your crop, then display it on your badge.");
  }
  $("photo").addEventListener("change",()=>onFile($("photo").files[0]).catch(error=>feedback(error.message,"error")));
  $("zoom").addEventListener("input",event=>{
    const old=zoom;zoom=Number(event.target.value);
    panX*=zoom/old;panY*=zoom/old;
    $("zoomValue").textContent=zoom.toFixed(1)+"×";renderPhoto();
  });
  canvas.addEventListener("pointerdown",event=>{
    if(!bitmap)return;drag={x:event.clientX,y:event.clientY,px:panX,py:panY};
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove",event=>{
    if(!drag)return;
    const bounds=canvas.getBoundingClientRect();
    panX=drag.px+(event.clientX-drag.x)*160/bounds.width;
    panY=drag.py+(event.clientY-drag.y)*120/bounds.height;
    renderPhoto();
  });
  ["pointerup","pointercancel","lostpointercapture"].forEach(name=>canvas.addEventListener(name,()=>{drag=null;}));
  function ipv4(value) {
    const parts=value.trim().split(".");
    return parts.length===4 && parts.every(p=>/^\d{1,3}$/.test(p)&&Number(p)<=255);
  }
  $("openBadge").addEventListener("click",()=>{
    const address=$("address").value.trim();
    if (!ipv4(address)) { feedback("Enter the four-part IP address displayed on your badge (for example, 192.168.1.42).","error");return; }
    location.assign("http://"+address+":8080/");
  });
  $("address").addEventListener("keydown",event=>{if(event.key==="Enter")$("openBadge").click();});
  async function plain(path,options={}) {
    const response=await fetch(path,{...options,cache:"no-store"});
    let value;
    try { value=await response.json(); } catch { throw Error("Badge returned an invalid response."); }
    if (!response.ok) throw Error(value.error || "Badge returned an error.");
    return value;
  }
  async function signed(method,path,body=new Uint8Array(),query="") {
    if(!keyHex||!deviceId)throw Error("Pair this browser with your badge first.");
    const challenge=await plain("/api/challenge?device_id="+encodeURIComponent(deviceId));
    const nonce=challenge.nonce,rawBody=bytes(body),key=fromHex(keyHex);
    const request=enc.encode(method+"\n"+path+"\n"+nonce+"\n"+hex(sha256(rawBody)));
    const signature=hex(hmac(key,request));
    const options={
      method,headers:{"X-Work-Device":deviceId,"X-Work-Nonce":nonce,"X-Work-Signature":signature},
      cache:"no-store"
    };
    if(method!=="GET"){
      options.body=rawBody;
      options.headers["Content-Type"]=path==="/api/frame/chunk"?"application/octet-stream":"application/json";
    }
    const response=await fetch(path+query,options);
    const buffer=new Uint8Array(await response.arrayBuffer());
    const supplied=response.headers.get("X-Work-Signature") || "";
    const expected=hex(hmac(key,enc.encode("response\n"+nonce+"\n"+hex(sha256(buffer)))));
    if(!/^[0-9a-f]{64}$/i.test(supplied)||!timingEqual(fromHex(supplied.toLowerCase()),fromHex(expected)))
      throw Error("Badge response was not authenticated.");
    let value;
    try {value=JSON.parse(new TextDecoder().decode(buffer));}catch{throw Error("Invalid badge response.");}
    if(!response.ok)throw Error(value.error||"Badge rejected the request.");
    return value;
  }
  async function checkBadge() {
    const status=await signed("GET","/api/frame");
    $("connectionName").textContent="Connected to "+host;
    $("connectionHint").textContent=status.has_photo?"A photo is currently on your badge.":"Your badge is ready for its first photo.";
    $("connectionPill").textContent="CONNECTED";
    $("connectionPill").classList.add("connected");
    $("pairPanel").hidden=true;$("pairedPanel").hidden=false;updateSend();
    return status;
  }
  async function restore() {
    if(!deviceId||!keyHex)return;
    try {await checkBadge();feedback("Connected to your paired badge.","success");}
    catch(error) {
      deviceId=null;keyHex=null;
      localStorage.removeItem(storeId);localStorage.removeItem(storeKey);
      updateSend();feedback("Previous pairing is unavailable. Pair this browser again. "+error.message,"error");
    }
  }
  async function pair() {
    if(busy)return;
    busy=true;$("pair").disabled=true;
    let id,secret;
    try {
      id=hex(random(16));
      const privateKey=random(32);
      const base=new Uint8Array(32);base[0]=9;
      const publicKey=x25519(privateKey,base);
      const result=await plain("/api/pair",{
        method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({device_id:id,device_name:$("deviceName").value.trim().slice(0,16)||"Photo controller",public_key:hex(publicKey)})
      });
      const badgePublic=fromHex(result.public_key);
      if(badgePublic.length!==32)throw Error("Invalid badge pairing key.");
      const transcript=concat(enc.encode("work-status-pair-v1\0"),enc.encode(id),publicKey,badgePublic);
      const shared=x25519(privateKey,badgePublic);
      if(shared.every(value=>value===0))throw Error("Invalid shared pairing key.");
      secret=hex(sha256(concat(enc.encode("work-status-key-v1\0"),shared,transcript)));
      $("pairCode").textContent=result.code.slice(0,3)+" "+result.code.slice(3);
      $("pairInstructions").hidden=false;feedback("Check the code on your badge and press UP to approve.");
      let approved=false;
      for(let attempt=0;attempt<27;attempt++){
        await delay(1000);
        const status=await plain("/api/pair/status?device_id="+id);
        if(status.status==="approved"){approved=true;break;}
        if(status.status==="rejected"||status.status==="expired"||status.status==="unknown")
          throw Error("Pairing was rejected or expired. Try again.");
      }
      if(!approved)throw Error("Pairing timed out. Start again.");
      deviceId=id;keyHex=secret;
      localStorage.setItem(storeId,id);localStorage.setItem(storeKey,secret);
      await checkBadge();$("pairInstructions").hidden=true;
      feedback("Paired successfully. Select a photo to send.","success");
    }catch(error){feedback(error.message,"error");}
    finally{busy=false;$("pair").disabled=false;updateSend();}
  }
  $("pair").addEventListener("click",pair);
  $("refresh").addEventListener("click",()=>checkBadge().then(()=>feedback("Badge connected.","success")).catch(error=>feedback(error.message,"error")));
  async function sendPhoto(){
    if(busy||!bitmap||!keyHex)return;
    busy=true;updateSend();$("progress").style.width="0%";
    const progress=document.querySelector(".progress-track");progress.setAttribute("aria-valuenow","0");
    try{
      renderPhoto();
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));
      if(!blob)throw Error("Your browser could not encode this picture.");
      const image=new Uint8Array(await blob.arrayBuffer());
      if(image.length>98304)throw Error("This PNG is too large for the badge. Choose a simpler image.");
      feedback("Transferring "+Math.ceil(image.length/1024)+" KB over your home Wi-Fi…");
      await signed("POST","/api/frame/start",JSON.stringify({size:image.length,sha256:hex(sha256(image))}));
      let offset=0;
      while(offset<image.length){
        const next=image.subarray(offset,Math.min(offset+768,image.length));
        const result=await signed("POST","/api/frame/chunk",next,"?offset="+offset);
        if(result.offset!==offset+next.length)throw Error("Unexpected transfer offset from badge.");
        offset=result.offset;
        const percent=Math.round(offset/image.length*95);
        $("progress").style.width=percent+"%";progress.setAttribute("aria-valuenow",String(percent));
      }
      feedback("Verifying and displaying the picture…");
      await signed("POST","/api/frame/finish","{}");
      $("progress").style.width="100%";progress.setAttribute("aria-valuenow","100");
      feedback("Done! Your photo is now displayed on the badge.","success");
    }catch(error){feedback("Upload did not finish: "+error.message,"error");}
    finally{busy=false;updateSend();}
  }
  $("send").addEventListener("click",sendPhoto);
  renderPhoto();
  $("hosted").hidden=localMode;
  $("local").hidden=!localMode;
  if(localMode)restore();
  else {
    $("photo").disabled=true;
    $("photoName").textContent="Connect to your badge first";
    $("photoInfo").textContent="Photo selection is available after opening the badge controller.";
    feedback("Open Photo Frame on your badge and enter its local IP address to connect.");
  }
})();