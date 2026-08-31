/* ================= IRA Credit Risk - JS (new template) ================= */
(function () {
  "use strict";
  var RC = { "Very Low":"#18a66a","Low":"#8ccf4d","Medium":"#e6a62f","High":"#ef8a3c","Very High":"#df514b","Not Available":"#9aa6bf","Not Applicable":"#9aa6bf" };
  var CATS = ["Secured","Unsecured","SME Banking","Wealth Lending","Wealth Lending - Retail Banking","Wealth Lending - PvB"];
  var PAGES = ["login","upload","validation","run","missing","output","final"];
  var KICKER = { login:"ACCESS", upload:"INPUT DATA", validation:"QUALITY GATE", run:"READY TO PROCESS", missing:"DATA COMPLETENESS", output:"CALCULATED RESULT", final:"FINAL DECISION" };

  var files = { mi:null, other:null, config:null };
  var PSID = null, DATA = null, RUN_ID = null, VALID = null;
  var overrides = {};
  var reached = { login:true };

  function bu(p){ try { return getWebAppBackendUrl(p); } catch(e){ return p; } }
  var $=function(s,r){return (r||document).querySelector(s);};
  var $$=function(s,r){return [].slice.call((r||document).querySelectorAll(s));};
  function esc(v){return v==null?"":String(v).replace(/[&<>]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;"}[c];});}
  function toast(m){var t=$("#toast");t.textContent=m;t.hidden=false;clearTimeout(t._t);t._t=setTimeout(function(){t.hidden=true;},2600);}
  function rateChip(r){ return (r&&RC[r]) ? '<span class="rate" style="background:'+RC[r]+'">'+esc(r)+'</span>' : esc(r||""); }

  /* ---------- navigation ---------- */
  function unlock(page){ reached[page]=true; var b=$('.nav-item[data-page="'+page+'"]'); if(b) b.disabled=false; }
  function go(page){
    if(!reached[page]) return;
    PAGES.forEach(function(p){ $("#page-"+p).classList.toggle("active", p===page); });
    $$(".nav-item").forEach(function(b){
      var p=b.getAttribute("data-page");
      b.classList.toggle("active", p===page);
      b.classList.toggle("done", reached[p] && p!==page && PAGES.indexOf(p)<PAGES.indexOf(page));
    });
    var titles={login:["Welcome to IRA Credit Risk","Sign in with your PSID to start a controlled assessment run."],
      upload:["Upload assessment datasets","Provide the MI workbook, other tables and country configuration."],
      validation:["Dataset validation","Confirm required tables and cross-table schemas."],
      run:["Run assessment","Process the validated datasets through the IRA engine."],
      missing:["Missing data","Fill any Not-Available values, or skip to the assessment output."],
      output:["Assessment output","Review the calculated output. Period: "+((DATA&&DATA.meta&&DATA.meta.mi_period)||"—")+"."],
      final:["Final Inherent Credit Risk Assessment","Record the final rating for every Product and Country."]};
    $("#pageKicker").textContent=KICKER[page]||"";
    $("#pageTitle").textContent=titles[page][0];
    $("#pageSubtitle").textContent=titles[page][1];
    window.scrollTo({top:0,behavior:"smooth"});
  }
  $$(".nav-item").forEach(function(b){ b.addEventListener("click",function(){ go(b.getAttribute("data-page")); }); });
  $$("[data-go]").forEach(function(b){ b.addEventListener("click",function(){ go(b.getAttribute("data-go")); }); });
  $("#btnReset").addEventListener("click",function(){ location.reload(); });

  /* ---------- login ---------- */
  $("#loginForm").addEventListener("submit",function(e){
    e.preventDefault();
    var psid=$("#psid").value.trim();
    fetch(bu("/login"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({psid:psid})})
      .then(function(r){return r.json();}).then(function(res){
        if(!res.ok){ toast(res.error||"Invalid PSID"); return; }
        PSID=res.psid;
        $("#sessionPsid").textContent="PSID "+PSID;
        $("#sessionState").textContent="Session active";
        $(".pulse").classList.add("live");
        $("#btnReset").hidden=false;
        unlock("upload"); go("upload");
      }).catch(function(){ toast("Login failed"); });
  });

  /* ---------- uploads ---------- */
  $$(".upload-card").forEach(function(card){
    var key=card.getAttribute("data-key"), input=$("input",card);
    card.addEventListener("click",function(e){ if(e.target!==input) input.click(); });
    input.addEventListener("change",function(){ if(input.files[0]) setFile(key,input.files[0],card); });
    ["dragover"].forEach(function(ev){card.addEventListener(ev,function(e){e.preventDefault();card.style.borderColor="#1769ff";});});
    ["dragleave","drop"].forEach(function(ev){card.addEventListener(ev,function(e){e.preventDefault();card.style.borderColor="";});});
    card.addEventListener("drop",function(e){ var f=e.dataTransfer.files[0]; if(f) setFile(key,f,card); });
  });
  function setFile(key,file,card){
    files[key]=file; card.classList.add("ready");
    $(".file-state",card).textContent="✓ "+file.name;
    var n=["mi","other","config"].filter(function(k){return files[k];}).length;
    $("#uploadCount").textContent=n;
    $("#btnValidate").disabled = n<3;
  }
  if($("#year") && !$("#year").value) $("#year").value=new Date().getFullYear();

  $("#btnValidate").addEventListener("click", runValidation);
  $("#btnRevalidate").addEventListener("click", runValidation);
  function runValidation(){
    var fd=new FormData(); fd.append("mi",files.mi); fd.append("other",files.other); fd.append("config",files.config);
    unlock("validation"); go("validation");
    $("#validationSummary").className="status-badge neutral"; $("#validationSummary").textContent="Checking…";
    fetch(bu("/validate"),{method:"POST",body:fd}).then(function(r){return r.json();}).then(function(v){
      VALID=v; renderValidation(v);
    }).catch(function(e){ toast("Validation failed: "+e); });
  }
  function renderValidation(v){
    var req=v.required||[], sch=v.schema||[];
    var reqFound=req.filter(function(x){return x.found;}).length;
    var schIssues=sch.filter(function(x){return !x.ok;});
    var missing=req.filter(function(x){return !x.found;});
    // stats
    $("#validationStats").innerHTML=[
      stat("Required tables", reqFound+"/"+req.length, reqFound===req.length?"good":"bad"),
      stat("Schema checks", (sch.length-schIssues.length)+"/"+sch.length, schIssues.length?"bad":"good"),
      stat("Missing tables", missing.length, missing.length?"bad":"good"),
      stat("Column issues", schIssues.reduce(function(a,x){return a+(x.missing||[]).length;},0), schIssues.length?"bad":"good")
    ].join("");
    // required table checklist (with a View button per available table)
    var h="<table><thead><tr><th>Group</th><th>Table</th><th>Available</th><th>View</th></tr></thead><tbody>";
    req.forEach(function(x){
      var view=x.found?"<button class='btn ghost small view-tbl' data-tname=\""+esc(x.table)+"\">⧉ View</button>":"<span class='muted'>—</span>";
      h+="<tr><td>"+esc(x.group||"")+"</td><td>"+esc(x.table)+"</td><td class='check "+(x.found?"ok":"bad")+"'>"+(x.found?"✓ Found":"✗ Missing")+"</td><td>"+view+"</td></tr>";
    });
    $("#tableChecks").innerHTML=h+"</tbody></table>";
    Array.prototype.forEach.call(document.querySelectorAll(".view-tbl"),function(b){
      b.addEventListener("click",function(){ viewTableFor(b.getAttribute("data-tname")); });
    });
    // schema checks
    var s="<table><thead><tr><th>Table</th><th>Status</th><th>Missing columns</th><th>Columns found</th></tr></thead><tbody>";
    sch.forEach(function(x){ s+="<tr><td>"+esc(x.table)+"</td><td class='check "+(x.ok?"ok":"bad")+"'>"+(x.ok?"✓ Match":"✗ Issue")+"</td><td>"+((x.missing||[]).map(esc).join(", ")||"—")+"</td><td class='muted'>"+((x.found_columns||[]).map(esc).join(", "))+"</td></tr>"; });
    if(!sch.length) s+="<tr><td colspan='4' class='muted'>No reference tables to schema-check.</td></tr>";
    $("#schemaChecks").innerHTML=s+"</tbody></table>";
    // issues
    var issues=[];
    missing.forEach(function(x){ issues.push({t:x.table,c:"Table not found in the uploaded workbooks."}); });
    schIssues.forEach(function(x){ (x.missing||[]).forEach(function(col){ issues.push({t:x.table,c:"Missing column: "+col}); }); });
    $("#issueCount").textContent=issues.length;
    $("#issuePanel").classList.toggle("clear", issues.length===0);
    $("#issues").innerHTML = issues.length ? issues.map(function(i){ return "<div class='issue'><div><b>"+esc(i.t)+"</b>"+esc(i.c)+"</div></div>"; }).join("")
      : "<p class='muted'>All checks passed. No blocking issues.</p>";
    // summary + gate
    var ok = (missing.length===0 && schIssues.length===0);
    $("#validationSummary").className="status-badge "+(ok?"good":"bad");
    $("#validationSummary").textContent = ok?"All checks passed":"Issues found";
    $("#btnGoRun").disabled = !ok;
    if(ok){ unlock("run"); }
    loadTablePreview();
  }
  // ---- parsed-table viewer (after upload) ----
  var TABLES=[]; var pendingView=null;
  function normName(s){ return String(s||"").toLowerCase().replace(/[^a-z0-9]+/g,""); }
  function viewTableFor(name){
    if(!TABLES.length){ pendingView=name; loadTablePreview(); return; }
    var n=normName(name), idx=-1, best=-1;
    TABLES.forEach(function(t,i){ var tn=normName(t.name);
      if(tn===n){ idx=i; }
      else if(idx<0 && (tn.indexOf(n)>=0 || n.indexOf(tn)>=0) && best<0){ best=i; } });
    if(idx<0) idx=(best>=0?best:0);
    var sel=$("#tableSelect"); if(sel) sel.value=String(idx);
    renderTable(TABLES[idx]);
    var card=$("#tablesCard"); if(card) card.scrollIntoView({behavior:"smooth",block:"start"});
  }
  function loadTablePreview(){
    if(!files.mi){ return; }
    var fd=new FormData(); fd.append("mi",files.mi); if(files.other) fd.append("other",files.other);
    $("#tableView").innerHTML="<p class='muted'>Loading parsed tables…</p>";
    fetch(bu("/tables_preview"),{method:"POST",body:fd}).then(function(r){return r.json();}).then(function(res){
      if(!res.ok){ $("#tableView").innerHTML="<p class='muted'>Could not parse tables: "+esc(res.error||"")+"</p>"; return; }
      TABLES=res.tables||[];
      $("#tablesCount").textContent=TABLES.length;
      var sel=$("#tableSelect"); sel.innerHTML="";
      TABLES.forEach(function(t,i){ var o=document.createElement("option"); o.value=String(i); o.textContent=t.name+"  ("+t.rows.length+" rows)"; sel.appendChild(o); });
      sel.onchange=function(){ renderTable(TABLES[parseInt(sel.value,10)]); };
      if(TABLES.length){ renderTable(TABLES[0]); } else { $("#tableView").innerHTML="<p class='muted'>No tables parsed.</p>"; }
      if(pendingView){ var pv=pendingView; pendingView=null; viewTableFor(pv); }
    }).catch(function(e){ $("#tableView").innerHTML="<p class='muted'>Preview failed: "+esc(String(e))+"</p>"; });
  }
  function renderTable(t){
    if(!t){ return; }
    var h="<table><thead><tr>"; t.columns.forEach(function(c){ h+="<th>"+esc(c)+"</th>"; }); h+="</tr></thead><tbody>";
    t.rows.slice(0,500).forEach(function(r){ h+="<tr>"; r.forEach(function(v){ h+="<td>"+esc(v)+"</td>"; }); h+="</tr>"; });
    h+="</tbody></table>";
    if(t.rows.length>500){ h+="<p class='muted'>Showing first 500 of "+t.rows.length+" rows. Use Copy for all.</p>"; }
    $("#tableView").innerHTML=h;
  }
  function tableToTSV(t){
    var lines=[t.columns.join("\t")];
    t.rows.forEach(function(r){ lines.push(r.map(function(v){ return v==null?"":String(v); }).join("\t")); });
    return lines.join("\n");
  }
  $("#btnCopyTable").addEventListener("click",function(){
    if(!TABLES.length){ toast("No table to copy"); return; }
    var t=TABLES[parseInt($("#tableSelect").value||"0",10)]; var tsv=tableToTSV(t);
    function done(){ toast('Copied "'+t.name+'" ('+t.rows.length+" rows)"); }
    if(navigator.clipboard && navigator.clipboard.writeText){ navigator.clipboard.writeText(tsv).then(done,function(){ fallbackCopy(tsv,done); }); }
    else { fallbackCopy(tsv,done); }
  });
  function fallbackCopy(text,cb){ var ta=document.createElement("textarea"); ta.value=text; document.body.appendChild(ta); ta.select(); try{document.execCommand("copy");}catch(e){} document.body.removeChild(ta); if(cb)cb(); }
  function stat(label,val,kind){ return "<div class='stat "+(kind||"")+"'><small>"+esc(label)+"</small><strong>"+esc(val)+"</strong></div>"; }

  $("#btnGoRun").addEventListener("click",function(){ unlock("run"); go("run"); renderRecap(); });

  /* ---------- run ---------- */
  function renderRecap(){
    $("#runRecap").innerHTML = "<span>PSID: "+esc(PSID)+"</span><span>Quarter: "+esc($('#quarter').value)+"</span><span>Year: "+esc($('#year').value)+"</span>";
  }
  $("#btnRun").addEventListener("click",function(){
    $("#runProgress").hidden=false; $("#btnRun").disabled=true;
    var steps=["Preparing datasets…","Parsing country and product…","Building formulas…","Assembling assessment…"], i=0;
    var iv=setInterval(function(){ $("#runProgressText").textContent=steps[i%steps.length]; i++; },700);
    var fd=new FormData(); fd.append("mi",files.mi); fd.append("other",files.other); fd.append("config",files.config);
    fd.append("user",PSID||"unknown"); fd.append("quarter",$("#quarter").value); fd.append("year",$("#year").value);
    fetch(bu("/analyze"),{method:"POST",body:fd}).then(function(r){return r.json();}).then(function(res){
      clearInterval(iv); $("#runProgress").hidden=true; $("#btnRun").disabled=false;
      if(!res.ok){ toast(res.error||"Run failed"); return; }
      DATA=res; RUN_ID=res.run_id; overrides={};
      $("#runChip").hidden=false; $("#runChip").textContent="Period "+(res.meta.mi_period||"");
      buildOutput(); buildFinal();
      unlock("missing"); unlock("output"); unlock("final"); go("missing");
    }).catch(function(e){ clearInterval(iv); $("#runProgress").hidden=true; $("#btnRun").disabled=false; toast("Run failed: "+e); });
  });

  /* ---------- output ---------- */
  function buildOutput(){
    var sp=$("#productFilter"); sp.innerHTML="";
    CATS.forEach(function(c){ if((DATA.countries_by_product[c]||[]).length){ var o=document.createElement("option"); o.value=c;o.textContent=c; sp.appendChild(o);} });
    outFillCountries(); renderOutStats(); renderResult();
    sp.onchange=function(){ outFillCountries(); renderResult(); };
    $("#countryFilter").onchange=renderResult;
    loadNA();
    var rb=$("#btnRefreshNA"); if(rb) rb.onclick=loadNA;
  }
  // ---- issue 2: manually feed Not-Available numbers ----
  function loadNA(){
    if(!RUN_ID){ return; }
    var host=$("#manualList"); if(!host) return;
    host.innerHTML="<p class='muted'>Loading…</p>";
    fetch(bu("/na_values/"+encodeURIComponent(RUN_ID))).then(function(r){return r.json();}).then(function(res){
      if(!res.ok){ host.innerHTML="<p class='muted'>"+esc(res.error||"Could not load")+"</p>"; return; }
      renderNA(res.na||[], res.manual||[]);
    }).catch(function(e){ host.innerHTML="<p class='muted'>"+esc(String(e))+"</p>"; });
  }
  function renderNA(na, manual){
    var host=$("#manualList");
    var badge=$("#missingBadge"); if(badge){ var nn=(na||[]).length; badge.textContent=nn+" missing"; }
    var doneKeys={}; (manual||[]).forEach(function(m){ doneKeys[m.Product+"||"+m.Country+"||"+m.Label]=m; });
    if(!na.length && !(manual||[]).length){ host.innerHTML="<p class='muted'>No Not-Available values — nothing to feed.</p>"; return; }
    var h="<table><thead><tr><th>Product</th><th>Country</th><th>Label</th><th>Value</th><th></th></tr></thead><tbody>";
    (manual||[]).forEach(function(m){
      h+="<tr style='background:#f6fff9'><td>"+esc(m.Product)+"</td><td>"+esc(m.Country)+"</td><td>"+esc(m.Label)+"</td><td>"+esc(m["Manual Value"])+" "+rateChip(m["Risk Rating"])+"</td><td class='muted'>manually put number</td></tr>";
    });
    na.forEach(function(x,i){
      var id="man"+i;
      h+="<tr><td>"+esc(x.product)+"</td><td>"+esc(x.country)+"</td><td>"+esc(x.label)+"</td>"+
         "<td><input class='input' id='"+id+"' style='width:110px' placeholder='e.g. 0.02'></td>"+
         "<td><button class='btn ghost small' data-p=\""+esc(x.product)+"\" data-c=\""+esc(x.country)+"\" data-l=\""+esc(x.label)+"\" data-i='"+id+"'>Feed &amp; recalc</button></td></tr>";
    });
    host.innerHTML=h+"</tbody></table>";
    Array.prototype.forEach.call(host.querySelectorAll("button[data-i]"),function(b){
      b.onclick=function(){
        var val=($("#"+b.getAttribute("data-i"))||{}).value;
        if(val===undefined||val===""){ toast("Enter a number"); return; }
        submitManual(b.getAttribute("data-p"),b.getAttribute("data-c"),b.getAttribute("data-l"),val);
      };
    });
  }
  function submitManual(product,country,label,value){
    post("/set_manual/"+encodeURIComponent(RUN_ID),{product:product,country:country,label:label,value:value}).then(function(r){
      if(!r.ok){ toast(r.error||"Failed"); return; }
      toast("Fed "+product+" · "+country+" — recalculated");
      // reflect in the cached results so the output table updates
      var rows=((DATA.results[product]||{})[country])||[];
      rows.forEach(function(row){
        if(row.label===label){ row.value=r.value; row.rating=r.rating; row.number=r.number; }
        if(row.calc && r.calculated){ row.value=r.calculated.value; row.rating=r.calculated.rating; }
      });
      if(DATA.calculated&&DATA.calculated[product]) DATA.calculated[product][country]=r.calculated;
      if(curProd()===product && curCountry()===country) renderResult();
      loadNA();
    });
  }
  function curProd(){ return $("#productFilter").value; }
  function curCountry(){ return $("#countryFilter").value; }
  function outFillCountries(){
    var sc=$("#countryFilter"); sc.innerHTML="";
    (DATA.countries_by_product[curProd()]||[]).forEach(function(c){ var o=document.createElement("option"); o.value=c;o.textContent=c; sc.appendChild(o); });
  }
  function renderOutStats(){
    var m=DATA.meta;
    $("#outputStats").innerHTML=[
      stat("Period (from MI)", m.mi_period||"—",""),
      stat("Products", Object.keys(DATA.results).filter(function(p){return (DATA.countries_by_product[p]||[]).length;}).length,""),
      stat("Countries", m.n_countries,""),
      stat("Data gaps", m.na_count, m.na_count?"bad":"good")
    ].join("");
  }
  function renderResult(){
    var rows=((DATA.results[curProd()]||{})[curCountry()])||[];
    var h="<table><thead><tr><th>Period</th><th>Label</th><th>Value</th><th>Risk Rating</th><th>Risk Number</th></tr></thead><tbody>";
    rows.forEach(function(r){
      var cls=r.calc?"style='font-weight:800;background:#f6f9ff'":(r.override?"style='background:#fff8ec'":"");
      h+="<tr "+cls+"><td>"+esc(DATA.meta.mi_period||"")+"</td><td>"+esc(r.label)+"</td><td>"+(r.value==null||r.value===""?"—":esc(r.value))+"</td><td>"+rateChip(r.rating)+"</td><td>"+(r.number==null?"":esc(r.number))+"</td></tr>";
    });
    $("#resultTable").innerHTML=h+"</tbody></table>";
  }
  $("#btnDownload").addEventListener("click",function(){
    if(!RUN_ID){ toast("Run first"); return; }
    window.open(bu("/download_period/"+encodeURIComponent(RUN_ID)),"_blank");
    toast("Downloading output (with Period "+(DATA.meta.mi_period||"")+")");
  });
  $("#btnIntermediate").addEventListener("click",function(){
    if(!RUN_ID){ toast("Run first"); return; }
    window.open(bu("/download_intermediate/"+encodeURIComponent(RUN_ID)),"_blank");
    toast("Downloading intermediate tables (with Period)");
  });
  $("#btnTrace").addEventListener("click",function(){
    if(!RUN_ID){ toast("Run first"); return; }
    window.open(bu("/download_trace/"+encodeURIComponent(RUN_ID)),"_blank");
    toast("Downloading Trace (relevant values per country)");
  });
  $("#btnLogic").addEventListener("click",function(){
    if(!RUN_ID){ toast("Run first"); return; }
    window.open(bu("/download_logic/"+encodeURIComponent(RUN_ID)),"_blank");
    toast("Downloading full IRA calculation logic");
  });
  $("#btnFlow").addEventListener("click",function(){
    var card=$("#flowCard");
    if(!card.hidden){ card.hidden=true; return; }
    card.hidden=false;
    if(card._loaded){ return; }
    fetch(bu("/lineage/"+encodeURIComponent(RUN_ID))).then(function(r){return r.json();}).then(function(res){
      if(!res.ok){ toast("Flow unavailable"); return; }
      card._rows=res.rows; card._loaded=true;
      var sp=$("#flowProduct"); sp.innerHTML="";
      CATS.forEach(function(c){ var o=document.createElement("option"); o.value=c;o.textContent=c; sp.appendChild(o); });
      sp.onchange=function(){ renderFlow(card._rows, sp.value); };
      renderFlow(card._rows, sp.value);
      card.scrollIntoView({behavior:"smooth"});
    }).catch(function(){ toast("Flow failed"); });
  });
  function renderFlow(rows, product){
    var r=rows.filter(function(x){return x.product===product;});
    var h="<table><thead><tr><th>Label</th><th>Raw sheet(s) — L1</th><th>Parsed table(s) — L2</th><th>Formula — L3</th><th>Applies</th></tr></thead><tbody>";
    r.forEach(function(x){
      h+="<tr><td>"+esc(x.label)+"</td>"+
         "<td><span class='rate' style='background:#1769ff'>"+esc(x.raw)+"</span></td>"+
         "<td><span class='rate' style='background:#00a99d'>"+esc(x.parsed)+"</span></td>"+
         "<td><span class='rate' style='background:#e6a62f'>"+esc(x.formula)+"</span></td>"+
         "<td class='check "+(x.applicable?"ok":"bad")+"'>"+(x.applicable?"✓":"—")+"</td></tr>";
    });
    $("#flowTable").innerHTML=h+"</tbody></table>";
  }

  /* ---------- final decisions ---------- */
  function buildFinal(){
    var sp=$("#finalProduct"); sp.innerHTML="";
    CATS.forEach(function(c){ if((DATA.countries_by_product[c]||[]).length){ var o=document.createElement("option"); o.value=c;o.textContent=c; sp.appendChild(o);} });
    finalFillCountries();
    sp.onchange=function(){ finalFillCountries(); loadDecision(); };
    $("#finalCountry").onchange=loadDecision;
    $("#btnSaveDecision").onclick=saveDecision;
    $("#btnFinalize").onclick=finalize;
    var of=$("#overrideFile");
    if(of) of.onchange=function(){
      var f=of.files&&of.files[0]; if(!f) return;
      var fd=new FormData(); fd.append("file",f);
      fetch(bu("/upload_overrides/"+encodeURIComponent(RUN_ID)),{method:"POST",body:fd}).then(function(r){return r.json();}).then(function(res){
        if(!res.ok){ toast(res.error||"Upload failed"); return; }
        (res.overrides||[]).forEach(function(o){
          overrides[o.product]=overrides[o.product]||{};
          overrides[o.product][o.country]={override_rating:o.override_rating, override_text:o.override_text};
        });
        toast("Loaded "+res.count+" overrides from file");
        loadDecision(); renderTracker(); updateCompletion();
      }).catch(function(e){ toast(String(e)); });
      of.value="";
    };
    loadDecision(); renderTracker();
  }
  function fProd(){ return $("#finalProduct").value; }
  function fCountry(){ return $("#finalCountry").value; }
  function finalFillCountries(){
    var sc=$("#finalCountry"); sc.innerHTML="";
    (DATA.countries_by_product[fProd()]||[]).forEach(function(c){ var o=document.createElement("option"); o.value=c;o.textContent=c; sc.appendChild(o); });
  }
  function calcFor(p,c){ return ((DATA.calculated||{})[p]||{})[c]||{}; }
  function loadDecision(){
    var cc=calcFor(fProd(),fCountry());
    $("#calculatedRating").innerHTML=rateChip(cc.rating)||"—";
    $("#calculatedScore").textContent = cc.value==null?"—":("Score "+cc.value);
    var ov=(overrides[fProd()]||{})[fCountry()]||{};
    $("#finalRating").value=ov.override_rating||"";
    $("#finalComment").value=ov.override_text||"";
    $("#decisionSaved").textContent="";
    var total=scopeCount(), done=doneCount();
    $("#scopeCounter").textContent=done+" of "+total+" saved";
  }
  function saveDecision(){
    var r=$("#finalRating").value, txt=$("#finalComment").value.trim();
    if(!r && !txt){ toast("Select a rating or add a rationale"); return; }
    overrides[fProd()]=overrides[fProd()]||{};
    overrides[fProd()][fCountry()]={override_rating:r, override_text:txt};
    $("#decisionSaved").textContent="Saved "+fProd()+" · "+fCountry();
    renderTracker(); updateCompletion();
  }
  function scopeCount(){ var n=0; CATS.forEach(function(p){ n+=(DATA.countries_by_product[p]||[]).length; }); return n; }
  function doneCount(){ var n=0; Object.keys(overrides).forEach(function(p){ n+=Object.keys(overrides[p]).length; }); return n; }
  function updateCompletion(){
    var pct=scopeCount()?Math.round(100*doneCount()/scopeCount()):0;
    $("#completionBadge").textContent=pct+"% complete";
    $("#btnFinalize").disabled = doneCount()===0;
  }
  function renderTracker(){
    var h="<table><thead><tr><th>Product</th><th>Country</th><th>Final rating</th><th>Rationale</th></tr></thead><tbody>";
    var any=false;
    CATS.forEach(function(p){ (DATA.countries_by_product[p]||[]).forEach(function(c){
      var ov=(overrides[p]||{})[c]; if(!ov) return; any=true;
      h+="<tr><td>"+esc(p)+"</td><td>"+esc(c)+"</td><td>"+rateChip(ov.override_rating||"(kept)")+"</td><td class='muted'>"+esc(ov.override_text||"")+"</td></tr>";
    }); });
    if(!any) h+="<tr><td colspan='4' class='muted'>No decisions saved yet.</td></tr>";
    $("#decisionTracker").innerHTML=h+"</tbody></table>";
    updateCompletion();
  }
  function finalize(){
    var payload=[];
    Object.keys(overrides).forEach(function(p){ Object.keys(overrides[p]).forEach(function(c){
      var o=overrides[p][c];
      if((o.override_rating&&o.override_rating!=="")||(o.override_text&&o.override_text!=="")){
        payload.push({product:p,country:c,override_rating:o.override_rating,override_text:o.override_text});
      }
    }); });
    post("/approve",{run_id:RUN_ID,overrides:payload}).then(function(r){
      logLine(r.ok?("Approved & finalized. Overrides saved: "+r.overrides_saved):("Approve failed: "+esc(r.error)));
      if(r.ok){
        post("/save_output",{run_id:RUN_ID}).then(function(s){ logLine(s.ok?("Output saved to folder: "+esc(s.path)):("Save failed: "+esc(s.error))); });
        post("/trigger_tableau",{run_id:RUN_ID}).then(function(tb){ logLine(tb.ok?("Tableau scenario triggered"):("Tableau: "+esc(tb.error))); });
      }
      toast(r.ok?"Finalized":"Failed");
    });
  }
  function logLine(msg){ var d=document.createElement("div"); d.className="line"; d.innerHTML=msg; $("#finalLog").appendChild(d); }
  function post(path,body){ return fetch(bu(path),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(function(r){return r.json();}).catch(function(e){return {ok:false,error:String(e)};}); }
})();