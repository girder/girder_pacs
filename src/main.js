import Girder, { RestClient } from '@girder/components';
import Vue from 'vue';
import App from './App.vue';
import router from './router';

Vue.use(Girder);

const girderRest = new RestClient({ apiRoot: GIRDER_API_ROOT });
girderRest.fetchUser().then(() => {
  new Vue({
    render: h => h(App),
    router,
    provide: { girderRest },
  }).$mount('#app');
});
