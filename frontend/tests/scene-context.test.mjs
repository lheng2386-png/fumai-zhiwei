import assert from 'node:assert/strict';
import {demoManifest,validateManifest} from '../dist/scene-data.js';
import {contextLayout} from '../dist/scene-context-data.js';
const assets=validateManifest(demoManifest([])).assets;
const bounds={min:{x:Math.min(...assets.map(a=>a.position[0])),z:Math.min(...assets.map(a=>a.position[2]))},max:{x:Math.max(...assets.map(a=>a.position[0])),z:Math.max(...assets.map(a=>a.position[2]))}};
const layout=contextLayout(bounds),{plot,buildings,trees}=layout;
assert.equal(layout.source,'constructed_context');assert.deepEqual(layout,contextLayout(bounds));
assert.equal(buildings.length,3);assert.ok(trees.length>=60);
for(const b of buildings){
    assert.ok(b.x+b.width/2<plot.minX||b.x-b.width/2>plot.maxX||b.z+b.depth/2<plot.minZ||b.z-b.depth/2>plot.maxZ,'Buildings must stay outside the station fence');
}
for(const t of trees){
    assert.ok(t.x+t.radius<plot.minX||t.x-t.radius>plot.maxX||t.z+t.radius<plot.minZ||t.z-t.radius>plot.maxZ,'Tree crowns must stay outside the equipment enclosure');
    assert.ok(!buildings.some(b=>Math.abs(t.x-b.x)<b.width/2+t.radius+6&&Math.abs(t.z-b.z)<b.depth/2+t.radius+6),'Tree clearances must preserve building yards');
    assert.ok(!(t.x>plot.maxX&&Math.abs(t.z)<8),'Keep the entry road clear');
}
for(const value of[null,{min:{x:NaN,z:0},max:{x:1,z:1}},{min:{x:2,z:0},max:{x:1,z:1}}])assert.throws(()=>contextLayout(value));
console.log(JSON.stringify({buildings:buildings.length,trees:trees.length,deterministic:true,perimeterAndYardClearances:'passed',invalidBounds:3,source:layout.source}));
