function randomSequence(seed) {
    return () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
}

/** Constructed perimeter context, in local metres; never an engineering asset inventory. */
export function contextLayout(bounds) {
    const values = [bounds?.min?.x,bounds?.max?.x,bounds?.min?.z,bounds?.max?.z];
    if (!values.every(Number.isFinite) || values[0] > values[1] || values[2] > values[3]) throw new TypeError('周边示意环境需要有效场景边界');
    const plot = {minX:bounds.min.x-14,maxX:bounds.max.x+14,minZ:bounds.min.z-14,maxZ:bounds.max.z+14};
    const width = plot.maxX-plot.minX;
    const buildings = [
        {id:'context_factory_1',kind:'factory',x:plot.minX+width*.25,z:plot.maxZ+34,width:50,depth:26,height:8.5},
        {id:'context_factory_2',kind:'factory',x:plot.minX+width*.65,z:plot.maxZ+38,width:62,depth:30,height:10.5},
        {id:'context_operations',kind:'operations',x:plot.maxX+38,z:18,width:24,depth:16,height:5.5}
    ];
    const trees = [],random = randomSequence(8435);
    function tree(x,z) {
        const radius = 2.1+random()*.9,height = 5.2+random()*2.6;
        if (!(x+radius < plot.minX || x-radius > plot.maxX || z+radius < plot.minZ || z-radius > plot.maxZ)) return;
        if (buildings.some(b => Math.abs(x-b.x) < b.width/2+radius+6 && Math.abs(z-b.z) < b.depth/2+radius+6)) return;
        if (x > plot.maxX && Math.abs(z) < 8 || Math.abs(x-(plot.maxX+16)) < 7) return;
        trees.push({x,z,radius,height,turn:random()*Math.PI*2,tint:random()});
    }
    for (let x=plot.minX-4; x<plot.maxX; x+=14) {
        tree(x+(random()-.5)*3,plot.minZ-22+(random()-.5)*3);
        tree(x+(random()-.5)*3,plot.maxZ+68+(random()-.5)*4);
    }
    for (let z=plot.minZ+7; z<plot.maxZ+45; z+=15) {
        tree(plot.minX-20+(random()-.5)*3,z);
        tree(plot.maxX+72+(random()-.5)*4,z);
    }
    for (let i=0;i<16;i++) tree(plot.minX-38-random()*22,plot.minZ+20+random()*95);
    return {source:'constructed_context',plot,buildings,trees};
}
