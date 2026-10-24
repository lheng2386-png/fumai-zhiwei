import fs from 'node:fs';
import assert from 'node:assert/strict';
const recipe=JSON.parse(fs.readFileSync(new URL('../source/equipment-recipe.json',import.meta.url)));
const layout=JSON.parse(fs.readFileSync(new URL('../source/layout.json',import.meta.url)));
assert.equal(recipe.source,'constructed_demo');assert.equal(recipe.engineering_accuracy,'unverified_no_drawings_or_survey');assert.equal(recipe.units,'m');assert.equal(recipe.up,'Y');
const materials=new Set(recipe.materials.map(m=>m.id));assert.equal(materials.size,recipe.materials.length);
const triple=x=>Array.isArray(x)&&x.length===3&&x.every(Number.isFinite);
for(const model of recipe.models){assert.ok(model.parts.length>=14);assert.ok(triple(model.dimensions_m));assert.ok(model.parts.some(p=>!p.detail));for(const part of model.parts){assert.ok(materials.has(part.material));if(part.shape==='box'){assert.ok(triple(part.center)&&triple(part.size)&&part.size.every(v=>v>0));}else if(part.shape==='cylinder'){assert.ok(triple(part.start)&&triple(part.end)&&part.radius>0);assert.ok(Math.hypot(...part.end.map((v,i)=>v-part.start[i]))>0);}else{assert.equal(part.shape,'torus');assert.ok(part.tube>0&&part.radius>part.tube);}}}
assert.equal(layout.assets.length,602);assert.equal(new Set(layout.assets.map(a=>a.id)).size,602);
const ids=new Set(layout.assets.map(a=>a.id));for(const asset of layout.assets){assert.ok(triple(asset.position)&&triple(asset.rotation));assert.ok(recipe.models.some(m=>m.id===asset.type));if(asset.group)assert.ok(ids.has(asset.group));}
const inv=recipe.models.find(m=>m.id==='inverter'),transformer=recipe.models.find(m=>m.id==='transformer');assert.ok(inv.parts.some(p=>p.name==='door_hinge')&&inv.parts.some(p=>p.name==='cable_gland'));assert.ok(transformer.parts.some(p=>p.name==='cooling_fin')&&transformer.parts.some(p=>p.name==='insulator_disc'));
const layoutInverter=layout.assets.find(a=>a.id==='inv_07');assert.deepEqual(layoutInverter.position,[-25.3,0,-3.4000000000000004]);
console.log(JSON.stringify({assetIdentities:602,modelParts:Object.fromEntries(recipe.models.map(m=>[m.id,m.parts.length])),dimensions:'illustrative',nativeBuild:'not_asserted_by_source_tests'}));
