import assert from 'node:assert/strict';
import {equipmentRecipe as recipe} from '../dist/scene-equipment-data.js';

assert.equal(recipe.source,'constructed_demo');
assert.equal(recipe.units,'m');
assert.equal(recipe.up,'Y');
assert.match(recipe.engineering_accuracy,/unverified/);
const materials=new Set(recipe.materials.map(m=>m.id));
assert.equal(materials.size,recipe.materials.length);
const vector=v=>Array.isArray(v)&&v.length===3&&v.every(Number.isFinite);
let parts=0;
for(const model of recipe.models){
    assert.ok(['panel','rack','inverter','transformer'].includes(model.id));
    for(const part of model.parts){
        parts++;
        assert.ok(materials.has(part.material),'Every surface must resolve a material');
        if(part.shape==='box'){
            assert.ok(vector(part.center)&&vector(part.size)&&part.size.every(n=>n>0));
            assert.ok(!part.bevel||part.bevel>=0);
        }else if(part.shape==='cylinder'){
            assert.ok(vector(part.start)&&vector(part.end)&&part.radius>0);
            assert.notDeepEqual(part.start,part.end,'Cylinder endpoints must not coincide');
        }else if(part.shape==='torus'){
            assert.ok(vector(part.center)&&part.radius>part.tube&&part.tube>0);
            assert.ok(!part.axis||['x','z'].includes(part.axis));
        }else assert.fail('Unknown equipment primitive');
    }
}
assert.equal(new Set(recipe.models.map(m=>m.id)).size,4);
console.log(JSON.stringify({geometryParts:parts,materials:materials.size,dimensions:'valid',provenance:'constructed_demo'}));
